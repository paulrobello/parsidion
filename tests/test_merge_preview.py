"""Tests for vault_merge dry-run preview caching and execute-path locking.

Covers two enhancements:
- A dry-run merge that produces an AI-merged body caches it to
  ``<vault>/.merge_previews/<a-stem>--<b-stem>.json``. A later
  ``--execute --from-preview`` reuses that cached body (skipping the AI
  call) when both source notes are unchanged, and falls back to a fresh AI
  call when either note has changed since the preview was written. The
  cache entry is removed after a successful execute.
- ``--execute`` guards the write/trash/backlink sequence with an exclusive,
  non-blocking lock file so a second concurrent ``--execute`` fails cleanly
  instead of blocking.
"""

from __future__ import annotations

import fcntl
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
import vault_merge  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_vault(tmp_vault: Path) -> None:  # noqa: ARG001
    """Wire resolve_vault() to a fresh tmp dir via the tmp_vault fixture."""


@pytest.fixture()
def vault(tmp_vault: Path) -> Path:
    """Create standard vault directories and return vault root."""
    for d in vault_common.VAULT_DIRS:
        (tmp_vault / d).mkdir(exist_ok=True)
    return tmp_vault


_NOTE_A = (
    "---\ndate: 2026-01-01\ntype: pattern\ntags: [python]\nrelated: []\n---\n"
    "# Note A\n\nUnique content from A.\n"
)
_NOTE_B = (
    "---\ndate: 2026-01-02\ntype: pattern\ntags: [vault]\nrelated: []\n---\n"
    "# Note B\n\nUnique content from B.\n"
)

_AI_BODY_V1 = "## Summary\n\nMerged content covering both notes, version one.\n"
_AI_BODY_V2 = "## Summary\n\nMerged content covering both notes, version two.\n"


def _make_pair(vault: Path) -> tuple[Path, Path]:
    path_a = vault / "Patterns" / "note-a.md"
    path_b = vault / "Patterns" / "note-b.md"
    path_a.write_text(_NOTE_A, encoding="utf-8")
    path_b.write_text(_NOTE_B, encoding="utf-8")
    return path_a, path_b


def _run_main(monkeypatch: pytest.MonkeyPatch, vault: Path, *extra: str) -> None:
    argv = [
        "vault-merge",
        "--vault",
        str(vault),
        "note-a",
        "note-b",
        "--no-index",
        *extra,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    vault_merge.main()


def _preview_path(vault: Path) -> Path:
    return vault / ".merge_previews" / "note-a--note-b.json"


# ---------------------------------------------------------------------------
# Dry-run preview caching
# ---------------------------------------------------------------------------


class TestPreviewWrittenOnDryRun:
    def test_preview_file_written_with_ai_body_and_hashes(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        calls: list[int] = []

        def _fake_run_ai_prompt(*_a: object, **_k: object) -> str:
            calls.append(1)
            return _AI_BODY_V1

        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", _fake_run_ai_prompt
        )
        _run_main(monkeypatch, vault)  # dry-run (no --execute)

        assert len(calls) == 1
        preview_file = _preview_path(vault)
        assert preview_file.exists()
        # Directory is created with restrictive permissions.
        assert (vault / ".merge_previews").stat().st_mode & 0o777 == 0o700

        payload = json.loads(preview_file.read_text(encoding="utf-8"))
        assert payload["body"] == _AI_BODY_V1
        assert payload["hash_a"] == vault_merge._hash_content(
            path_a.read_text(encoding="utf-8")
        )
        assert payload["hash_b"] == vault_merge._hash_content(
            path_b.read_text(encoding="utf-8")
        )

    def test_no_preview_when_ai_unavailable(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: None
        )
        _run_main(monkeypatch, vault)
        assert not _preview_path(vault).exists()

    def test_no_preview_with_no_ai_flag(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pair(vault)

        def _fail(*_a: object, **_k: object) -> str:
            raise AssertionError("AI backend must not be called with --no-ai")

        monkeypatch.setattr(vault_merge.ai_backend, "run_ai_prompt", _fail)
        _run_main(monkeypatch, vault, "--no-ai")
        assert not _preview_path(vault).exists()


class TestFromPreviewReuse:
    def test_execute_from_preview_reuses_cached_body_without_ai_call(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
        )
        _run_main(monkeypatch, vault)  # dry-run writes the preview
        assert _preview_path(vault).exists()

        def _fail(*_a: object, **_k: object) -> str:
            raise AssertionError("AI backend must not be called on preview reuse")

        monkeypatch.setattr(vault_merge.ai_backend, "run_ai_prompt", _fail)
        _run_main(monkeypatch, vault, "--execute", "--from-preview")

        merged = path_a.read_text(encoding="utf-8")
        assert "Merged content covering both notes, version one." in merged
        assert not path_b.exists()
        assert (vault / ".trash" / "note-b.md").exists()

    def test_preview_deleted_after_successful_execute(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
        )
        _run_main(monkeypatch, vault)
        assert _preview_path(vault).exists()

        _run_main(monkeypatch, vault, "--execute", "--from-preview")
        assert not _preview_path(vault).exists()

    def test_stale_hash_falls_back_to_fresh_ai_call(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
        )
        _run_main(monkeypatch, vault)  # dry-run writes the preview
        assert _preview_path(vault).exists()

        # Note A changes after the preview was generated -> hash_a mismatch.
        path_a.write_text(
            _NOTE_A.replace("Unique content from A.", "Edited content from A."),
            encoding="utf-8",
        )

        calls: list[int] = []

        def _fake_run_ai_prompt(*_a: object, **_k: object) -> str:
            calls.append(1)
            return _AI_BODY_V2

        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", _fake_run_ai_prompt
        )
        _run_main(monkeypatch, vault, "--execute", "--from-preview")

        assert len(calls) == 1  # fresh AI call was made
        merged = path_a.read_text(encoding="utf-8")
        assert "Merged content covering both notes, version two." in merged
        err = capsys.readouterr().err
        assert "falling back to a fresh AI merge" in err
        assert not path_b.exists()

    def test_no_preview_present_falls_back_to_fresh_ai_call(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V2
        )
        assert not _preview_path(vault).exists()

        _run_main(monkeypatch, vault, "--execute", "--from-preview")

        merged = path_a.read_text(encoding="utf-8")
        assert "Merged content covering both notes, version two." in merged
        err = capsys.readouterr().err
        assert "No matching cached preview" in err
        assert not path_b.exists()


# ---------------------------------------------------------------------------
# Preview cache helpers (unit-level)
# ---------------------------------------------------------------------------


class TestPreviewHelpers:
    def test_load_fresh_preview_returns_none_when_missing(self, vault: Path) -> None:
        path_a, path_b = _make_pair(vault)
        result = vault_merge._load_fresh_preview(
            vault, path_a, _NOTE_A, path_b, _NOTE_B
        )
        assert result is None

    def test_write_then_load_round_trips(self, vault: Path) -> None:
        path_a, path_b = _make_pair(vault)
        vault_merge._write_preview(vault, path_a, _NOTE_A, path_b, _NOTE_B, _AI_BODY_V1)
        result = vault_merge._load_fresh_preview(
            vault, path_a, _NOTE_A, path_b, _NOTE_B
        )
        assert result == _AI_BODY_V1

    def test_load_fresh_preview_none_on_hash_b_mismatch(self, vault: Path) -> None:
        path_a, path_b = _make_pair(vault)
        vault_merge._write_preview(vault, path_a, _NOTE_A, path_b, _NOTE_B, _AI_BODY_V1)
        result = vault_merge._load_fresh_preview(
            vault, path_a, _NOTE_A, path_b, "changed body"
        )
        assert result is None


# ---------------------------------------------------------------------------
# Execute-path locking
# ---------------------------------------------------------------------------


class TestMergeLock:
    def test_held_lock_causes_clean_failure_not_deadlock(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        lock_path = vault / ".merge_previews" / ".merge.lock"
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)
        held = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            monkeypatch.setattr(
                vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
            )
            with pytest.raises(SystemExit) as exc_info:
                _run_main(monkeypatch, vault, "--execute")
            assert exc_info.value.code == 1
            # Nothing was mutated: the second invocation failed before
            # touching note A/B.
            assert path_a.read_text(encoding="utf-8") == _NOTE_A
            assert path_b.exists()
            assert not (vault / ".trash").exists()
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            held.close()

    def test_lock_released_after_successful_execute_allows_next_merge(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
        )
        _run_main(monkeypatch, vault, "--execute")

        # The execute actually merged: keeper holds the AI body, loser trashed.
        merged = path_a.read_text(encoding="utf-8")
        assert "Merged content covering both notes, version one." in merged
        assert not path_b.exists()
        assert (vault / ".trash" / "note-b.md").exists()

        # Lock must be released; a fresh acquire attempt must not block/fail.
        lock_path = vault / ".merge_previews" / ".merge.lock"
        probe = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
        finally:
            probe.close()

    def test_dry_run_does_not_take_the_lock(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _make_pair(vault)
        lock_path = vault / ".merge_previews" / ".merge.lock"
        lock_path.parent.mkdir(mode=0o700, exist_ok=True)
        held = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            monkeypatch.setattr(
                vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _AI_BODY_V1
            )
            # Dry-run (no --execute) must succeed even while the merge lock
            # is held, since it does not mutate keeper/loser/backlinks.
            _run_main(monkeypatch, vault)
            assert _preview_path(vault).exists()
        finally:
            fcntl.flock(held.fileno(), fcntl.LOCK_UN)
            held.close()
