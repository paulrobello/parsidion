"""Tests for note supersession (card 01a07c86): retirement honored everywhere.

Covers the frontmatter contract (note_schema.validate_status_fields), the
note_index SQL/walk exclusion with its include_superseded escape hatch, the
semantic-backend drop, backlink-suggestion exclusion, the doctor consistency
rule, and the vault-supersede CLI round trip.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_links  # noqa: E402
from core import parsight_backend  # noqa: E402
from core.vault_index import (  # noqa: E402
    ensure_note_index_schema,
    query_note_index,
)
from note_schema import (  # noqa: E402
    validate_status_fields,
)

LIVE_NOTE = {
    "stem": "live-note",
    "path": "",
    "folder": "Patterns",
    "title": "Live note",
    "summary": "",
    "tags": "vault, life",
    "note_type": "pattern",
    "project": "",
    "mtime": 1788000000.0,
    "related": "",
    "is_stale": 0,
    "incoming_links": 0,
    "date": "",
    "prompt_version": "",
    "incoming_stems": "[]",
    "status": "live",
}
RETIRED_NOTE = {
    **LIVE_NOTE,
    "stem": "retired-note",
    "title": "Retired note",
    "tags": "vault, death",
    "mtime": 1788000001.0,
    "status": "superseded",
}


def _write_note(
    vault: Path, stem: str, *, superseded_by: str | None = None, body: str = "Body."
) -> Path:
    extra = ""
    if superseded_by:
        extra = f'status: superseded\nsuperseded_by: ["[[{superseded_by}]]"]\n'
    note = vault / "Patterns" / f"{stem}.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ndate: 2026-09-07\ntype: pattern\ntags: [vault]\n"
        f'related: ["[[other-note]]"]\n{extra}---\n\n{body}\n',
        encoding="utf-8",
    )
    return note


@pytest.fixture()
def index_vault(tmp_vault: Path) -> Path:
    """A vault whose embeddings.db carries one live and one retired row."""
    live = _write_note(tmp_vault, "live-note")
    retired = _write_note(tmp_vault, "retired-note", superseded_by="live-note")
    conn = sqlite3.connect(str(tmp_vault / "embeddings.db"))
    ensure_note_index_schema(conn)
    conn.executemany(
        "INSERT INTO note_index (stem, path) VALUES (?, ?)",
        [
            ("live-note", str(live)),
            ("retired-note", str(retired)),
        ],
    )
    # Set the retired row's status explicitly (column defaults to live).
    conn.execute(
        "UPDATE note_index SET status = 'superseded' WHERE stem = 'retired-note'"
    )
    conn.commit()
    conn.close()
    return tmp_vault


class TestFrontmatterContract:
    def test_superseded_requires_superseded_by(self) -> None:
        errors = validate_status_fields({"status": "superseded"})
        assert errors and "superseded_by" in errors[0]

    def test_superseded_by_on_live_note_is_error(self) -> None:
        errors = validate_status_fields({"superseded_by": ["[[x]]"], "status": "live"})
        assert errors

    def test_valid_pair_clean(self) -> None:
        assert (
            validate_status_fields(
                {"status": "superseded", "superseded_by": ["[[new]]"]}
            )
            == []
        )

    def test_unknown_status_value_rejected(self) -> None:
        assert validate_status_fields({"status": "archived"})

    def test_canonical_order_contains_fields(self) -> None:
        from note_schema import FRONTMATTER_FIELD_ORDER

        order = list(FRONTMATTER_FIELD_ORDER)
        assert order.index("status") == order.index("superseded_by") - 1
        assert order.index("superseded_by") == order.index("session_id") - 1


class TestIndexExclusion:
    def test_query_note_index_excludes_by_default(self, index_vault: Path) -> None:
        stems = {p.stem for p in query_note_index(vault=index_vault) or []}
        assert "live-note" in stems
        assert "retired-note" not in stems

    def test_query_note_index_escape_hatch(self, index_vault: Path) -> None:
        stems = {
            p.stem
            for p in query_note_index(vault=index_vault, include_superseded=True) or []
        }
        assert {"live-note", "retired-note"} <= stems

    def test_snapshot_paths_where_excludes(self, index_vault: Path) -> None:
        from core.vault_index import load_session_index_snapshot

        snap = load_session_index_snapshot(index_vault)
        assert snap is not None
        stems = {p.stem for p in snap.paths_where(limit=100)}
        assert "retired-note" not in stems
        escaped = {p.stem for p in snap.paths_where(limit=100, include_superseded=True)}
        assert "retired-note" in escaped

    def test_snapshot_compact_map_excludes(self, index_vault: Path) -> None:
        from core.vault_index import load_session_index_snapshot

        snap = load_session_index_snapshot(index_vault)
        assert snap is not None
        assert "retired-note" not in snap.compact_index_map()
        assert "live-note" in snap.compact_index_map()

    def test_walk_fallback_excludes(self, tmp_vault: Path) -> None:
        _write_note(tmp_vault, "live-note")
        retired = _write_note(
            tmp_vault,
            "retired-note",
            superseded_by="live-note",
        )
        from core.vault_index import _find_notes_by_tag_walk

        tagged = _find_notes_by_tag_walk("vault", vault=tmp_vault)
        assert retired not in tagged
        assert tmp_vault / "Patterns" / "live-note.md" in tagged

    def test_recent_walk_excludes(self, tmp_vault: Path) -> None:
        """find_recent_notes' walk path skips retired notes too."""
        live = _write_note(tmp_vault, "live-note")
        retired = _write_note(
            tmp_vault,
            "retired-note",
            superseded_by="live-note",
        )
        from core.vault_index import _find_recent_notes_walk, all_vault_notes

        recent = _find_recent_notes_walk(30, vault=tmp_vault)
        assert retired not in recent
        assert live in recent
        # all_vault_notes stays complete — index rebuilds must see every file.
        assert retired in all_vault_notes(vault=tmp_vault)

    def test_pre_migration_db_degrades_gracefully(self, tmp_vault: Path) -> None:
        """An index without the status column must not fail the query."""
        conn = sqlite3.connect(str(tmp_vault / "embeddings.db"))
        conn.execute(
            "CREATE TABLE note_index (stem TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "mtime REAL NOT NULL DEFAULT 0.0)"
        )
        conn.execute(
            "INSERT INTO note_index VALUES ('legacy-note', '/tmp/legacy-note.md', 0.0)"
        )
        conn.commit()
        conn.close()
        result = query_note_index(vault=tmp_vault)
        assert result is not None  # table present, query served


class TestSemanticDrop:
    def test_parsight_search_drops_retired_stem(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            parsight_backend,
            "_load_note_index_rows",
            lambda stems, vault: {
                "live-note": {**LIVE_NOTE, "path": str(tmp_vault / "live-note.md")},
                "retired-note": {
                    **RETIRED_NOTE,
                    "path": str(tmp_vault / "retired-note.md"),
                },
            },
        )
        monkeypatch.setattr(
            parsight_backend,
            "find_code_raw",
            lambda *a, **k: [
                {"file_path": "live-note.md", "score": 1.0},
                {"file_path": "retired-note.md", "score": 0.9},
            ],
        )
        monkeypatch.setattr(
            parsight_backend, "resolve_decay_params", lambda vault: (None, None)
        )
        monkeypatch.setattr(parsight_backend, "_config_value", lambda *a, **k: False)
        results = parsight_backend.parsight_search("anything", vault=tmp_vault)
        assert results is not None
        stems = {r["stem"] for r in results}
        assert "retired-note" not in stems
        assert "live-note" in stems

    def test_embeddings_backend_skips_retired(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from cli.search import embeddings as cli_embeddings

        # The search bails out early when the DB file is absent.
        conn = sqlite3.connect(str(tmp_vault / "embeddings.db"))
        ensure_note_index_schema(conn)
        conn.commit()
        conn.close()

        class FakeConn:
            def execute(self, sql: str, params: object = None):
                assert "status = 'superseded'" in sql

                class R:
                    def fetchall(self):
                        return [("retired-note",)]

                return R()

            def close(self) -> None:
                pass

        retired_row = (
            "retired-note",
            "/vault/retired-note.md",
            "Patterns",
            "Retired",
            "vault",
            0.95,
            1788000000.0,
        )
        live_row = (
            "live-note",
            "/vault/live-note.md",
            "Patterns",
            "Live",
            "vault",
            0.90,
            1788000000.0,
        )
        monkeypatch.setattr(cli_embeddings, "_open_db_semantic", lambda p: FakeConn())
        monkeypatch.setattr(
            cli_embeddings,
            "_fetch_candidate_rows",
            lambda conn, blob, top: [live_row, retired_row],
        )
        monkeypatch.setattr(cli_embeddings, "_embed_query", lambda *a, **k: [0.0])
        monkeypatch.setattr(
            cli_embeddings, "_pack_vector", lambda vec: b"\x00" * len(vec)
        )
        monkeypatch.setattr(cli_embeddings, "get_config", lambda *a, **k: False)
        monkeypatch.setattr(
            cli_embeddings, "resolve_decay_params", lambda vault: (None, None)
        )
        results = cli_embeddings._search_embeddings("anything", vault=tmp_vault)
        stems = {r["stem"] for r in results}
        assert "retired-note" not in stems
        assert "live-note" in stems


class TestLinkSuggestions:
    def test_tag_overlap_never_suggests_retired(self, tmp_vault: Path) -> None:
        live = _write_note(tmp_vault, "live-note")
        retired = _write_note(tmp_vault, "retired-note", superseded_by="live-note")
        new_note = _write_note(tmp_vault, "brand-new-note")
        links = vault_links.find_related_by_tags(
            new_note,
            ["vault"],
            vault_notes=[live, retired, new_note],
            vault=tmp_vault,
        )
        assert links == ["[[live-note]]"]


class TestDoctorRule:
    def test_catalog_lists_the_rule(self) -> None:
        from doctor.protocol import RULE_SPECS

        assert any(s.name == "superseded-consistency" for s in RULE_SPECS)

    def test_check_flags_missing_superseded_by(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        note = _write_note(tmp_vault, "bad-note")
        note.write_text(
            "---\ndate: 2026-09-07\ntype: pattern\ntags: [vault]\n"
            'related: ["[[other-note]]"]\nstatus: superseded\n---\n\nBody.\n',
            encoding="utf-8",
        )
        issues = check_note(note, {}, tmp_vault)
        assert any("SUPERSEDED_CONSISTENCY" == i.code for i in issues)

    def test_check_flags_unresolvable_target(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        note = _write_note(tmp_vault, "bad-note", superseded_by="missing-note")
        issues = check_note(note, {}, tmp_vault)  # empty note_map: nothing resolves
        assert any(
            i.code == "SUPERSEDED_CONSISTENCY" and "does not resolve" in i.message
            for i in issues
        )

    def test_check_clean_for_consistent_pair(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        _write_note(tmp_vault, "replacement-note")
        note = _write_note(tmp_vault, "retired-note", superseded_by="replacement-note")
        issues = check_note(
            note,
            {"replacement-note": [tmp_vault / "Patterns" / "replacement-note.md"]},
            tmp_vault,
        )
        assert not [i for i in issues if i.code == "SUPERSEDED_CONSISTENCY"]


class TestSupersedeCli:
    def _run(self, vault: Path, *args: str) -> tuple[int, str]:
        import os
        import subprocess

        env = dict(os.environ)
        env["CLAUDE_VAULT"] = str(vault)
        env["XDG_CONFIG_HOME"] = str(vault / ".config")
        (vault / ".config" / "parsidion").mkdir(parents=True, exist_ok=True)
        (vault / ".config" / "parsidion" / "vaults.yaml").write_text(
            f"vaults:\n  t: {vault}\n", encoding="utf-8"
        )
        script = _SCRIPTS_DIR / "vault_supersede.py"
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(_SCRIPTS_DIR.parent.parent.parent),
        )
        return proc.returncode, proc.stdout + proc.stderr

    def test_retire_and_revert_round_trip(self, tmp_vault: Path) -> None:
        _write_note(tmp_vault, "old-note")
        _write_note(tmp_vault, "new-note")
        code, out = self._run(
            tmp_vault, "old-note", "new-note", "--reason", "test", "--execute"
        )
        assert code == 0, out
        retired = (tmp_vault / "Patterns" / "old-note.md").read_text(encoding="utf-8")
        assert "status: superseded" in retired
        assert "[[new-note]]" in retired
        code, out = self._run(tmp_vault, "old-note", "--revert", "--execute")
        assert code == 0, out
        restored = (tmp_vault / "Patterns" / "old-note.md").read_text(encoding="utf-8")
        assert "status:" not in restored

    def test_dry_run_writes_nothing(self, tmp_vault: Path) -> None:
        _write_note(tmp_vault, "old-note")
        _write_note(tmp_vault, "new-note")
        before = (tmp_vault / "Patterns" / "old-note.md").read_bytes()
        code, out = self._run(tmp_vault, "old-note", "new-note")
        assert code == 0, out
        assert (tmp_vault / "Patterns" / "old-note.md").read_bytes() == before

    def test_self_supersede_rejected(self, tmp_vault: Path) -> None:
        _write_note(tmp_vault, "same-note")
        code, out = self._run(tmp_vault, "same-note", "same-note", "--execute")
        assert code == 2


class TestConflictsResolution:
    def test_keep_a_supersedes_b_on_execute(self, tmp_vault: Path) -> None:
        import vault_conflicts

        a = _write_note(tmp_vault, "a-note")
        b = _write_note(tmp_vault, "b-note")
        conflict = {"a": str(a), "b": str(b)}
        result = vault_conflicts._apply_resolution(conflict, "keep_a", execute=True)
        assert "superseded by" in result
        content_b = b.read_text(encoding="utf-8")
        assert "status: superseded" in content_b
        assert "[[a-note]]" in content_b

    def test_preview_describes_without_writing(self, tmp_vault: Path) -> None:
        import vault_conflicts

        a = _write_note(tmp_vault, "a-note")
        b = _write_note(tmp_vault, "b-note")
        conflict = {"a": str(a), "b": str(b)}
        result = vault_conflicts._apply_resolution(conflict, "keep_a", execute=False)
        assert "would mark" in result
        assert "status: superseded" not in b.read_text(encoding="utf-8")
