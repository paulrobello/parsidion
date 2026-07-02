"""Regression tests for vault_merge / vault_metrics / vault_new bug fixes.

Covers:
- _ai_merge_bodies rejects backend refusal/error text (not a note body) and
  the merge aborts without writing note A or trashing note B.
- --execute writes the keeper atomically (tmp + replace); a failed replace
  leaves note A intact and note B untrashed.
- _update_wikilinks_in_vault unwraps the dangling [[loser]] link inside the
  keeper to plain display text instead of skipping the file.
- collect_timeline buckets by local calendar day, not rolling 24h windows.
- vault-new rejects titles that slugify to an empty string.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_merge  # noqa: E402
import vault_metrics  # noqa: E402
import vault_new  # noqa: E402


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

_REFUSAL = (
    "I'm sorry, but I can't merge these notes for you because the request "
    "appears to involve content I am unable to process at this time."
)


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


# ---------------------------------------------------------------------------
# Fix 1: AI refusal/error output must not be accepted as a merged body
# ---------------------------------------------------------------------------


class TestAIMergeOutputValidation:
    def test_refusal_text_raises(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _REFUSAL
        )
        with pytest.raises(vault_merge.AIMergeOutputError):
            vault_merge._ai_merge_bodies(path_a, path_b, "Note A", vault_path=vault)

    def test_refusal_logs_preview_to_stderr(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _REFUSAL
        )
        with pytest.raises(vault_merge.AIMergeOutputError):
            vault_merge._ai_merge_bodies(path_a, path_b, "Note A", vault_path=vault)
        err = capsys.readouterr().err
        assert _REFUSAL[:100] in err

    def test_unavailable_backend_returns_none(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: None
        )
        result = vault_merge._ai_merge_bodies(
            path_a, path_b, "Note A", vault_path=vault
        )
        assert result is None

    def test_valid_body_accepted(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        body = "## Summary\n\nMerged content covering both notes in detail.\n"
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: body
        )
        result = vault_merge._ai_merge_bodies(
            path_a, path_b, "Note A", vault_path=vault
        )
        assert result == body

    def test_execute_aborts_without_trashing_b(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        monkeypatch.setattr(
            vault_merge.ai_backend, "run_ai_prompt", lambda *a, **k: _REFUSAL
        )
        with pytest.raises(SystemExit) as exc_info:
            _run_main(monkeypatch, vault, "--execute")
        assert exc_info.value.code == 1
        # Note A untouched, note B still in place, nothing trashed.
        assert path_a.read_text(encoding="utf-8") == _NOTE_A
        assert path_b.exists()
        assert not (vault / ".trash").exists()


# ---------------------------------------------------------------------------
# Fix 2: atomic write of the merged note
# ---------------------------------------------------------------------------


class TestAtomicMergeWrite:
    def test_replace_failure_leaves_a_intact_and_b_untrashed(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)

        def _fail_replace(self: Path, target: Path) -> Path:
            raise OSError("simulated replace failure")

        monkeypatch.setattr(Path, "replace", _fail_replace)
        with pytest.raises(OSError, match="simulated replace failure"):
            _run_main(monkeypatch, vault, "--execute", "--no-ai")

        assert path_a.read_text(encoding="utf-8") == _NOTE_A
        assert path_b.exists()
        assert not (vault / ".trash").exists()
        # The temporary file must not linger in the vault.
        assert not (vault / "Patterns" / "note-a.md.tmp").exists()

    def test_successful_execute_writes_merge_and_trashes_b(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path_a, path_b = _make_pair(vault)
        _run_main(monkeypatch, vault, "--execute", "--no-ai")

        merged = path_a.read_text(encoding="utf-8")
        assert "Unique content from A." in merged
        assert "Unique content from B." in merged
        assert not path_b.exists()
        assert (vault / ".trash" / "note-b.md").exists()
        assert not (vault / "Patterns" / "note-a.md.tmp").exists()


# ---------------------------------------------------------------------------
# Fix 3: dangling [[loser]] link inside the keeper note
# ---------------------------------------------------------------------------


class TestKeeperWikilinkUnwrap:
    def test_keeper_link_unwrapped_to_plain_text(self, vault: Path) -> None:
        keeper = vault / "Patterns" / "note-a.md"
        keeper.write_text("# A\n\nSee [[note-b]] for background.\n", encoding="utf-8")
        updated = vault_merge._update_wikilinks_in_vault("note-b", "note-a", vault)
        assert updated == 1
        content = keeper.read_text(encoding="utf-8")
        assert "[[note-b]]" not in content
        assert "[[note-a]]" not in content  # no self-reference created
        assert "See note-b for background." in content

    def test_keeper_aliased_link_keeps_alias_text(self, vault: Path) -> None:
        keeper = vault / "Patterns" / "note-a.md"
        keeper.write_text("Read [[note-b|the old write-up]] first.\n", encoding="utf-8")
        vault_merge._update_wikilinks_in_vault("note-b", "note-a", vault)
        content = keeper.read_text(encoding="utf-8")
        assert "Read the old write-up first." in content
        assert "[[" not in content

    def test_other_files_still_rewritten_to_keeper(self, vault: Path) -> None:
        keeper = vault / "Patterns" / "note-a.md"
        keeper.write_text("# A\n", encoding="utf-8")
        other = vault / "Patterns" / "other.md"
        other.write_text("See [[note-b]].\n", encoding="utf-8")
        vault_merge._update_wikilinks_in_vault("note-b", "note-a", vault)
        assert "[[note-a]]" in other.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fix 4: collect_timeline calendar-day bucketing
# ---------------------------------------------------------------------------


class TestCollectTimelineCalendarBuckets:
    def _make_conn(self, mtimes: list[float]) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE note_index (mtime REAL)")
        conn.executemany(
            "INSERT INTO note_index (mtime) VALUES (?)", [(m,) for m in mtimes]
        )
        return conn

    def test_pre_midnight_mtime_lands_in_yesterday_bucket(self) -> None:
        today = date.today()
        yesterday = today - timedelta(days=1)
        # 23:30 yesterday: rolling 24h windows put this in "today" whenever
        # the report runs before 23:30; calendar bucketing must not.
        pre_midnight = datetime.combine(yesterday, dtime(23, 30)).timestamp()
        today_noonish = datetime.combine(today, dtime(0, 1)).timestamp()
        conn = self._make_conn([pre_midnight, today_noonish])

        result = vault_metrics.collect_timeline(conn, days=7)
        conn.close()

        assert len(result) == 7
        today_row = result[-1]
        yesterday_row = result[-2]
        assert today_row["date"] == today.strftime("%Y-%m-%d")
        assert today_row["is_today"] is True
        assert today_row["n"] == 1
        assert yesterday_row["date"] == yesterday.strftime("%Y-%m-%d")
        assert yesterday_row["n"] == 1

    def test_oldest_day_included_older_excluded(self) -> None:
        today = date.today()
        oldest = today - timedelta(days=6)
        in_range = datetime.combine(oldest, dtime(0, 5)).timestamp()
        out_of_range = datetime.combine(
            oldest - timedelta(days=1), dtime(23, 55)
        ).timestamp()
        conn = self._make_conn([in_range, out_of_range])

        result = vault_metrics.collect_timeline(conn, days=7)
        conn.close()

        assert result[0]["date"] == oldest.strftime("%Y-%m-%d")
        assert result[0]["n"] == 1
        assert sum(r["n"] for r in result) == 1


# ---------------------------------------------------------------------------
# Fix 6: vault-new rejects empty slugs
# ---------------------------------------------------------------------------


class TestVaultNewEmptySlug:
    def test_empty_slug_title_exits_with_error(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys, "argv", ["vault-new", "--type", "pattern", "--title", "!!! ???"]
        )
        with pytest.raises(SystemExit) as exc_info:
            vault_new.main()
        assert exc_info.value.code == 1
        assert "empty filename slug" in capsys.readouterr().err
        assert not (vault / "Patterns" / ".md").exists()

    def test_normal_title_still_creates_note(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sys, "argv", ["vault-new", "--type", "pattern", "--title", "My Pattern"]
        )
        vault_new.main()
        assert (vault / "Patterns" / "my-pattern.md").exists()
