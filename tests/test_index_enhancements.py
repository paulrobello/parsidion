"""Tests for three vault_index.py / update_index.py enhancements.

Covers:
- Frontmatter parse-warning collector (record_parse_warning/drain_parse_warnings)
  so stderr-only warnings become visible via hook events (`vault-stats --hooks N`).
- Non-ASCII-safe slugify (transliteration + stable-hash fallback for titles
  that transliterate to nothing, e.g. CJK-only titles).
- Race-free singleton guard in update_index.py (atomic O_CREAT|O_EXCL claim
  instead of check-then-write).
- ENH-004: DB-first note-index read path. Differential tests assert the
  DB path and the walk fallback return the same set, plus the fallback /
  stale-index / tampered-DB / --no-db escape-hatch cases.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

import update_index
import vault_common
import vault_index

# ---------------------------------------------------------------------------
# Parse warning collector
# ---------------------------------------------------------------------------


class TestParseWarningCollector:
    """Tests for vault_index.record_parse_warning / drain_parse_warnings."""

    @pytest.fixture(autouse=True)
    def _clear_collector(self) -> Generator[None]:
        # Ensure no warnings leak in/out across tests (module-level list).
        vault_index.drain_parse_warnings()
        yield
        vault_index.drain_parse_warnings()

    def test_record_and_drain(self) -> None:
        vault_index.record_parse_warning("warning one")
        vault_index.record_parse_warning("warning two")
        assert vault_index.drain_parse_warnings() == ["warning one", "warning two"]

    def test_drain_clears_collector(self) -> None:
        vault_index.record_parse_warning("warning")
        vault_index.drain_parse_warnings()
        assert vault_index.drain_parse_warnings() == []

    def test_cap_bounds_memory(self) -> None:
        for i in range(vault_index._PARSE_WARNINGS_MAX + 50):
            vault_index.record_parse_warning(f"warning {i}")
        drained = vault_index.drain_parse_warnings()
        assert len(drained) == vault_index._PARSE_WARNINGS_MAX
        assert drained[0] == "warning 0"

    def test_nested_mapping_warning_recorded(self) -> None:
        content = "---\nkey:\n  nested: value\n---\n"
        vault_index.parse_frontmatter(content)
        warnings = vault_index.drain_parse_warnings()
        assert len(warnings) == 1
        assert "nested" in warnings[0]

    def test_update_index_non_string_tag_warning_recorded(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notes_dir = tmp_vault / "Patterns"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "note.md").write_text(
            "---\ntags: [placeholder]\n---\n\n# Note\nBody.\n",
            encoding="utf-8",
        )
        # Simulate a legacy parser that coerced a list item to int.
        monkeypatch.setattr(
            update_index,
            "parse_frontmatter",
            lambda _content: {"tags": [2026, "python"]},
        )
        update_index.build_index(vault=tmp_vault)
        warnings = vault_index.drain_parse_warnings()
        assert any("coercing non-string tag" in w for w in warnings)


# ---------------------------------------------------------------------------
# slugify — non-ASCII safety
# ---------------------------------------------------------------------------


class TestSlugifyAsciiRegression:
    """Existing ASCII behavior must be unchanged (mirrors test_vault_common.py)."""

    def test_basic(self) -> None:
        assert vault_index.slugify("Hello World") == "hello-world"

    def test_underscores(self) -> None:
        assert vault_index.slugify("hello_world") == "hello-world"

    def test_special_characters(self) -> None:
        result = vault_index.slugify("Rust & WGPU: A Guide!")
        assert result == "rust-wgpu-a-guide"
        assert "--" not in result

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert vault_index.slugify("--hello--") == "hello"

    def test_multiple_hyphens_collapsed(self) -> None:
        assert vault_index.slugify("hello   world") == "hello-world"

    def test_empty_string(self) -> None:
        assert vault_index.slugify("") == ""

    def test_already_slugified(self) -> None:
        assert vault_index.slugify("already-slugified") == "already-slugified"

    def test_mixed_case(self) -> None:
        assert vault_index.slugify("CamelCase String") == "camelcase-string"

    def test_numbers_preserved(self) -> None:
        assert vault_index.slugify("wgpu 28 changes") == "wgpu-28-changes"

    def test_whitespace_stripped(self) -> None:
        assert vault_index.slugify("  padded  ") == "padded"


class TestSlugifyNonAscii:
    """Non-ASCII titles must transliterate or fall back to a stable hash."""

    def test_accented_transliterates(self) -> None:
        assert vault_index.slugify("Café Notes") == "cafe-notes"

    def test_accented_mixed(self) -> None:
        assert vault_index.slugify("Résumé Über Naïve") == "resume-uber-naive"

    def test_cjk_falls_back_to_stable_hash(self) -> None:
        result = vault_index.slugify("日本語")
        assert result.startswith("note-")
        assert len(result) == len("note-") + 8

    def test_cjk_fallback_is_stable(self) -> None:
        assert vault_index.slugify("日本語") == vault_index.slugify("日本語")

    def test_distinct_cjk_titles_get_different_slugs(self) -> None:
        slug_a = vault_index.slugify("日本語")
        slug_b = vault_index.slugify("中文标题")
        assert slug_a != slug_b
        assert slug_a.startswith("note-")
        assert slug_b.startswith("note-")


# ---------------------------------------------------------------------------
# Singleton guard — race-free PID claim
# ---------------------------------------------------------------------------


class TestSingletonGuardAtomicClaim:
    """Tests for update_index._write_pid / _singleton_guard.

    ARC-003: the vault path is now threaded explicitly through ``pid_file``,
    ``_write_pid``, ``_release_pid`` and ``_singleton_guard`` rather than
    read from ``vault_common.VAULT_ROOT``. The previous
    ``_patch_vault_root`` helper patched both ``vault_common.VAULT_ROOT``
    and ``update_index.VAULT_ROOT``; with the global no longer consulted
    by these functions, callers pass ``tmp_path`` directly.
    """

    def test_write_pid_atomic_create(self, tmp_path: Path) -> None:
        update_index._write_pid(tmp_path)
        pf = update_index.pid_file(tmp_path)
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_write_pid_second_claim_raises_file_exists(self, tmp_path: Path) -> None:
        update_index._write_pid(tmp_path)
        with pytest.raises(FileExistsError):
            update_index._write_pid(tmp_path)

    def test_singleton_guard_fresh_claim_succeeds(self, tmp_path: Path) -> None:
        update_index._singleton_guard(tmp_path)
        pf = update_index.pid_file(tmp_path)
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_singleton_guard_stale_pid_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pf = update_index.pid_file(tmp_path)
        pf.write_text("99999999", encoding="utf-8")  # bogus PID, treated as dead below
        monkeypatch.setattr(update_index, "_is_process_running", lambda pid: False)
        update_index._singleton_guard(tmp_path)
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_singleton_guard_live_pid_bails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_pid = os.getpid()  # genuinely running -- this test process itself
        pf = update_index.pid_file(tmp_path)
        pf.write_text(str(real_pid), encoding="utf-8")
        # Simulate "our" PID being different so the guard treats the file's
        # PID as belonging to another (genuinely alive) process rather than
        # hitting the self-exclusion branch.
        monkeypatch.setattr(update_index.os, "getpid", lambda: real_pid + 1)
        with pytest.raises(SystemExit) as exc_info:
            update_index._singleton_guard(tmp_path)
        assert exc_info.value.code == 0
        # The other process's PID file must be left untouched.
        assert pf.read_text(encoding="utf-8").strip() == str(real_pid)


# ---------------------------------------------------------------------------
# ENH-004 — DB-first note-index read path
# ---------------------------------------------------------------------------
#
# Differential tests: for each converted query function, the DB path and the
# walk fallback must return the same SET of notes. Plus the five enumerated
# edge cases (no DB, empty-vs-missing, tampered row, stale index, --no-db).


def _make_note(
    path: Path,
    *,
    note_type: str = "",
    project: str = "",
    tags: list[str] | None = None,
    mtime: float | None = None,
) -> None:
    """Write a minimal vault note with the given frontmatter fields."""
    tags = tags or []
    parts = ["---"]
    if note_type:
        parts.append(f"type: {note_type}")
    if project:
        parts.append(f"project: {project}")
    if tags:
        parts.append(f"tags: [{', '.join(tags)}]")
    parts.append("---\n")
    parts.append(f"# {path.stem}\nbody\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _build_index(tmp_vault: Path) -> None:
    """Populate embeddings.db/note_index via the real indexer.

    ``update_index.build_index`` builds the rows but does NOT persist them --
    persistence is ``main()``'s job (``_write_note_index_to_db`` at
    update_index.py:952). Mirror that here so the differential tests exercise
    the real writer, with rows that match exactly what ``_walk_vault_notes``
    discovers (build_index walks via ``all_vault_notes_walk``, the
    authoritative enumeration that never reads the DB it is populating).
    """
    db_path = vault_common.get_embeddings_db_path(tmp_vault)
    if not db_path.exists():
        conn = sqlite3.connect(str(db_path))
        try:
            vault_common.ensure_note_index_schema(conn)
        finally:
            conn.close()
    # build_index returns (index_md, total_notes, total_tags, folder_notes, db_rows, tag_counter)
    result = update_index.build_index(vault=tmp_vault)
    db_rows = result[4]
    current_stems = {row.stem for row in db_rows}
    update_index._write_note_index_to_db(db_rows, current_stems, vault=tmp_vault)


def _write_config(tmp_vault: Path, text: str) -> None:
    """Write config.yaml in the tmp vault and clear the config cache."""
    (tmp_vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.load_config.cache_clear()


_NOTES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "Patterns",
        "python-deco",
        dict(note_type="pattern", project="parsidion", tags=["python", "hook"]),
    ),
    (
        "Debugging",
        "rust-bug",
        dict(note_type="debugging", project="nerbs", tags=["rust"]),
    ),
    (
        "Patterns",
        "vault-idx",
        dict(note_type="pattern", project="parsidion", tags=["python", "vault"]),
    ),
]


def _seed_vault(tmp_vault: Path) -> list[Path]:
    """Create the three standard test notes; return their paths."""
    paths: list[Path] = []
    for folder, stem, fields in _NOTES:
        p = tmp_vault / folder / f"{stem}.md"
        _make_note(p, **fields)
        paths.append(p)
    return paths


class TestNoteIndexDbFirst:
    """ENH-004: DB-first read path with walk fallback."""

    def test_query_note_index_distinguishes_none_from_empty(
        self, tmp_vault: Path
    ) -> None:
        """Case 2: a missing DB returns None; an empty result returns []."""
        # No embeddings.db → None (signals fallback).
        assert vault_common.query_note_index(project="anything") is None

        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        # A project with zero notes → [] (NOT None → no fallback).
        result = vault_common.query_note_index(project="nonexistent-project")
        assert result is not None
        assert result == []

    @pytest.mark.parametrize(
        "fn_name, walk_name, arg",
        [
            ("find_notes_by_project", "_find_notes_by_project_walk", "parsidion"),
            ("find_notes_by_tag", "_find_notes_by_tag_walk", "python"),
            ("find_notes_by_type", "_find_notes_by_type_walk", "pattern"),
        ],
    )
    def test_db_and_walk_agree(
        self, tmp_vault: Path, fn_name: str, walk_name: str, arg: str
    ) -> None:
        """Differential: the DB path and the walk path return the same set."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)

        fn = getattr(vault_index, fn_name)
        walk = getattr(vault_index, walk_name)
        db_result = set(fn(arg, vault=tmp_vault))
        walk_result = set(walk(arg, vault=tmp_vault))
        assert db_result == walk_result, (
            f"{fn_name}({arg!r}) diverged: "
            f"db-only={db_result - walk_result} walk-only={walk_result - db_result}"
        )

    def test_all_vault_notes_db_first_matches_walk_on_current_index(
        self, tmp_vault: Path
    ) -> None:
        """Follow-up to ENH-004: all_vault_notes is now DB-first. On a current
        index it returns the same SET of notes as the authoritative walk."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        db_result = set(vault_index.all_vault_notes(tmp_vault))
        walk_result = set(vault_index.all_vault_notes_walk(tmp_vault))
        assert db_result == walk_result, (
            "all_vault_notes diverged from all_vault_notes_walk: "
            f"db-only={db_result - walk_result} walk-only={walk_result - db_result}"
        )

    def test_all_vault_notes_walk_is_always_authoritative(
        self, tmp_vault: Path
    ) -> None:
        """all_vault_notes_walk never reads the DB, so it sees notes a stale
        index misses -- this is why mutation paths (doctor/merge/export) and the
        index builders use it instead of the DB-first all_vault_notes."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        new_note = tmp_vault / "Patterns" / "walk-only-pattern.md"
        _make_note(
            new_note,
            note_type="pattern",
            project="parsidion",
            tags=["python"],
            mtime=time.time() + 3600,
        )
        # DB-first all_vault_notes misses the just-added note (stale index).
        assert new_note not in set(vault_index.all_vault_notes(tmp_vault))
        # The authoritative walk sees it.
        assert new_note in set(vault_index.all_vault_notes_walk(tmp_vault))

    def test_all_vault_notes_falls_back_to_walk_without_db(
        self, tmp_vault: Path
    ) -> None:
        """With embeddings.db absent, all_vault_notes walks -- the fallback the
        index builders rely on during a fresh build, before any DB exists."""
        _seed_vault(tmp_vault)
        # No _build_index -> no embeddings.db.
        walked = set(vault_index.all_vault_notes(tmp_vault))
        assert walked == {
            tmp_vault / "Patterns" / "python-deco.md",
            tmp_vault / "Debugging" / "rust-bug.md",
            tmp_vault / "Patterns" / "vault-idx.md",
        }

    def test_no_database_falls_back_to_walk(self, tmp_vault: Path) -> None:
        """Case 1: with embeddings.db absent, the walk fallback runs."""
        _seed_vault(tmp_vault)
        # No _build_index call → no embeddings.db.
        result = set(vault_index.find_notes_by_type("pattern", vault=tmp_vault))
        expected = {
            tmp_vault / "Patterns" / "python-deco.md",
            tmp_vault / "Patterns" / "vault-idx.md",
        }
        assert result == expected

    def test_tampered_db_row_outside_vault_is_filtered(self, tmp_vault: Path) -> None:
        """Case 3: a row whose path escapes the vault must be rejected by
        _paths_from_rows (the SEC-005 containment guard on DB reads)."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        # Insert a hostile row pointing at a real file outside the vault,
        # tagged as a pattern so find_notes_by_type("pattern") would surface
        # it absent the containment check.
        db_path = vault_common.get_embeddings_db_path(tmp_vault)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "INSERT OR REPLACE INTO note_index "
                "(stem, path, folder, title, tags, note_type, project) "
                "VALUES ('hostile', '/etc/passwd', 'x', 'h', '', 'pattern', '')"
            )
            conn.commit()
        finally:
            conn.close()
        # find_notes_by_type reads from the DB and must filter /etc/passwd out
        # (it exists, but is_path_inside_vault rejects it). Every returned
        # path must live inside the tmp vault.
        result = set(vault_index.find_notes_by_type("pattern", vault=tmp_vault))
        assert Path("/etc/passwd") not in result
        assert all(str(p).startswith(str(tmp_vault)) for p in result)
        # The legit pattern notes are still returned.
        assert tmp_vault / "Patterns" / "python-deco.md" in result

    def test_stale_index_missed_by_db_but_flagged_by_age(self, tmp_vault: Path) -> None:
        """Case 4: a note added after the last index rebuild is invisible to
        the DB path, and note_index_age() reports non-zero staleness."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        # Add a new pattern note on disk without reindexing; future-dated mtime
        # so it is unambiguously newer than anything in note_index.
        new_note = tmp_vault / "Patterns" / "fresh-pattern.md"
        _make_note(
            new_note,
            note_type="pattern",
            project="parsidion",
            tags=["python"],
            mtime=time.time() + 3600,
        )
        # DB path does not see it (index is stale).
        db_result = set(vault_index.find_notes_by_type("pattern", vault=tmp_vault))
        assert new_note not in db_result
        # Walk path does see it.
        walk_result = set(
            vault_index._find_notes_by_type_walk("pattern", vault=tmp_vault)
        )
        assert new_note in walk_result
        # Staleness signal is non-zero.
        assert vault_common.note_index_age(tmp_vault) > 0

    def test_config_no_db_forces_walk_and_finds_stale_note(
        self, tmp_vault: Path
    ) -> None:
        """Case 5: search.use_note_index=false forces the walk, which finds
        the note the stale DB missed."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        new_note = tmp_vault / "Patterns" / "config-walk-pattern.md"
        _make_note(
            new_note,
            note_type="pattern",
            project="parsidion",
            tags=["python"],
            mtime=time.time() + 7200,
        )
        _write_config(tmp_vault, "search:\n  use_note_index: false\n")
        # With the DB disabled at the config level, the walk runs and sees it.
        result = set(vault_index.find_notes_by_type("pattern", vault=tmp_vault))
        assert new_note in result

    # -- str-tolerance: the read-path surface accepts str OR Path for vault --

    @pytest.mark.parametrize(
        "fn_name, args",
        [
            ("all_vault_notes", ()),
            ("all_vault_notes_walk", ()),
            ("find_notes_by_type", ("pattern",)),
            ("find_notes_by_project", ("parsidion",)),
            ("find_notes_by_tag", ("python",)),
        ],
    )
    def test_read_path_accepts_str_and_path_vault(
        self, tmp_vault: Path, fn_name: str, args: tuple
    ) -> None:
        """The read-path surface is str-tolerant: str(vault) returns the same
        set as Path. Previously only query_note_index and _walk_vault_notes
        coerced; get_embeddings_db_path and note_index_age raised TypeError."""
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        fn = getattr(vault_index, fn_name)
        via_path = set(fn(*args, vault=tmp_vault))
        via_str = set(fn(*args, vault=str(tmp_vault)))
        assert via_path == via_str, f"{fn_name} diverged on str vs Path vault"

    def test_note_index_age_accepts_str(self, tmp_vault: Path) -> None:
        _seed_vault(tmp_vault)
        _build_index(tmp_vault)
        assert vault_common.note_index_age(tmp_vault) == vault_common.note_index_age(
            str(tmp_vault)
        )

    def test_get_embeddings_db_path_accepts_str(self, tmp_vault: Path) -> None:
        assert vault_common.get_embeddings_db_path(tmp_vault) == (
            vault_common.get_embeddings_db_path(str(tmp_vault))
        )
