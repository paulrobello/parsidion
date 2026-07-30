"""QA-016: tests for the destructive paths in vault_review.py.

``vault-review --clear`` empties the pending-sessions queue after an
interactive confirmation. The mutation was completely unverified before —
these tests pin both the confirm-empts and the cancel-preserves branches,
plus the ``--list`` no-mutation invariant and the queue-already-empty path.

The TUI itself is out of scope (curses + a real terminal); the destructive
``--clear`` flow is the highest-value coverage gap because it destroys queue
state silently if a regression swaps the branches.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_review  # noqa: E402


@pytest.fixture(autouse=True)
def _patch_vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point vault_review at the tmp vault.

    vault_review reads via vault_common.VAULT_ROOT (the module-level constant
    that resolve_vault() also re-points at). Patch that, not vault_review.
    """
    monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_vault)
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def vault(tmp_vault: Path) -> Path:
    return tmp_vault


def _seed_pending(vault: Path, entries: list[dict]) -> Path:
    """Write entries to <vault>/pending_summaries.jsonl and return its path."""
    pending = vault / "pending_summaries.jsonl"
    pending.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n" if entries else "",
        encoding="utf-8",
    )
    return pending


# ---------------------------------------------------------------------------
# _read_entries / _write_entries round-trip
# ---------------------------------------------------------------------------


class TestReadEntries:
    def test_returns_empty_when_queue_absent(self, vault: Path) -> None:
        assert vault_review._read_entries() == []

    def test_returns_entries_when_queue_present(self, vault: Path) -> None:
        _seed_pending(
            vault,
            [
                {"session_id": "a", "project": "x"},
                {"session_id": "b", "project": "y"},
            ],
        )
        result = vault_review._read_entries()
        assert len(result) == 2
        assert result[0]["session_id"] == "a"
        assert result[1]["session_id"] == "b"

    def test_skips_malformed_lines(self, vault: Path) -> None:
        _seed_pending(
            vault,
            [
                {"session_id": "ok"},
            ],
        )
        # Append a malformed line
        with open(vault / "pending_summaries.jsonl", "a", encoding="utf-8") as fh:
            fh.write("not-valid-json\n")
        result = vault_review._read_entries()
        assert len(result) == 1  # malformed line skipped


# ---------------------------------------------------------------------------
# _cmd_clear — the destructive path
# ---------------------------------------------------------------------------


class TestCmdClear:
    """Pin both branches of the confirmation gate so a regression that
    swaps them cannot silently destroy queue state."""

    def test_clear_with_y_confirmation_empties_queue(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending = _seed_pending(
            vault,
            [{"session_id": "a"}, {"session_id": "b"}, {"session_id": "c"}],
        )
        monkeypatch.setattr("builtins.input", lambda _prompt: "y")

        vault_review._cmd_clear(vault_path=vault)

        # Queue is now empty (file may be absent or zero-length).
        if pending.exists():
            assert pending.read_text(encoding="utf-8").strip() == ""

    def test_clear_with_n_cancellation_preserves_queue(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        entries = [{"session_id": "a"}, {"session_id": "b"}]
        pending = _seed_pending(vault, entries)
        original = pending.read_text(encoding="utf-8")
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        vault_review._cmd_clear(vault_path=vault)

        # The queue is byte-identical to the pre-call state.
        assert pending.read_text(encoding="utf-8") == original
        # And the in-memory read confirms no entries were dropped.
        assert len(vault_review._read_entries()) == 2

    def test_clear_with_empty_answer_cancels(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Empty / anything-not-'y' must cancel — the prompt is [y/N].
        entries = [{"session_id": "a"}]
        pending = _seed_pending(vault, entries)
        original = pending.read_text(encoding="utf-8")
        monkeypatch.setattr("builtins.input", lambda _prompt: "")

        vault_review._cmd_clear(vault_path=vault)

        assert pending.read_text(encoding="utf-8") == original

    def test_clear_on_already_empty_queue_no_ops(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # input() must NOT be called when the queue is empty.
        calls: list[str] = []

        def _no_input(prompt: str) -> str:
            calls.append(prompt)
            return "y"

        monkeypatch.setattr("builtins.input", _no_input)
        vault_review._cmd_clear(vault_path=vault)

        assert calls == [], "input() must not fire when queue is already empty"
        assert "already empty" in capsys.readouterr().out.lower()

    def test_clear_with_uppercase_Y_also_confirms(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The check is `answer != "y"` after `.strip().lower()` — uppercase
        # Y must also confirm. Pin that the lower-casing actually happens.
        _seed_pending(vault, [{"session_id": "a"}])
        monkeypatch.setattr("builtins.input", lambda _prompt: "Y")
        vault_review._cmd_clear(vault_path=vault)
        # Queue is empty after
        assert vault_review._read_entries() == []


# ---------------------------------------------------------------------------
# --list does not mutate
# ---------------------------------------------------------------------------


class TestListNoMutation:
    """``--list`` must be strictly read-only — destructive regressions here
    would silently empty the queue when the user only wanted to inspect."""

    def test_read_entries_does_not_write(self, vault: Path) -> None:
        entries = [{"session_id": "a"}, {"session_id": "b"}]
        pending = _seed_pending(vault, entries)
        mtime_before = pending.stat().st_mtime_ns

        # Read several times
        vault_review._read_entries()
        vault_review._read_entries()
        vault_review._read_entries()

        assert pending.stat().st_mtime_ns == mtime_before
