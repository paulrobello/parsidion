"""Unit tests for session_start_hook.py safety guards."""

from __future__ import annotations

import importlib
import io
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
session_start_hook = importlib.import_module("session_start_hook")


def _write_codex_config(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "config.yaml").write_text(
        "ai:\n"
        "  backend: codex-cli\n"
        "session_start_hook:\n"
        "  ai_model: null\n"
        "  ai_single_flight: false\n"
        "  ai_cooldown_seconds: 0\n"
        "  ai_timeout: 5\n"
        "  track_delta: false\n"
        "  use_embeddings: false\n",
        encoding="utf-8",
    )


def _run_session_start_main_for_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
) -> list[list[str]]:
    vault = tmp_path / "vault"
    project = tmp_path / "project"
    note = vault / "Projects" / "codex-note.md"
    project.mkdir(parents=True)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Codex Note\nUse Codex backend defaults.\n", encoding="utf-8")
    _write_codex_config(vault)

    session_start_hook.vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    session_start_hook.vault_common._clear_config_cache()
    monkeypatch.setenv("CLAUDE_VAULT", str(vault))
    monkeypatch.setattr(
        session_start_hook, "_build_candidates", lambda *_args, **_kwargs: [note]
    )
    monkeypatch.setattr(
        session_start_hook.vault_common, "write_hook_event", lambda **_kwargs: None
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text(
            "### Codex Note\nUse Codex backend defaults.", encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        session_start_hook.ai_backend, "_run_prompt_subprocess", fake_run
    )
    monkeypatch.setattr(sys, "argv", ["session_start_hook.py", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(project)})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    session_start_hook.main()
    assert calls
    return calls


class TestAiSelectionSafety:
    """Tests for SessionStart AI safety guards."""

    def test_skips_ai_when_single_flight_lock_is_busy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                True
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_try_acquire_ai_lock",
            lambda vault_path: None,
        )

        called = False

        def _fail_run_ai_prompt(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("AI backend should not run when the AI lock is busy")

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", _fail_run_ai_prompt
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

    def test_releases_lock_when_ai_backend_returns_no_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                True
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 1
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_try_acquire_ai_lock",
            lambda vault_path: object(),
        )

        released = False

        def _release(lock_file: object | None) -> None:
            nonlocal released
            released = True

        monkeypatch.setattr(session_start_hook, "_release_ai_lock", _release)
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "read_note_summary",
            lambda path, max_lines=6: "Useful summary",
        )
        calls: list[dict[str, object]] = []

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> None:
            calls.append({"prompt": prompt, **kwargs})
            return None

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        candidate = tmp_path / "note.md"
        candidate.write_text("ignored", encoding="utf-8")

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[candidate],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert calls[0]["timeout"] == 1
        assert released is True

    def test_skips_ai_when_cooldown_is_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_is_ai_cooldown_active",
            lambda vault_path: True,
        )

        called = False

        def _fail_run_ai_prompt(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("AI backend should not run while cooldown is active")

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", _fail_run_ai_prompt
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[tmp_path / "note.md"],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

    def test_select_context_with_ai_uses_small_tier_backend_and_writes_cooldown_stamp(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 7
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        note = tmp_path / "Patterns" / "codex-exec.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntags: [codex]\n---\n# Codex Exec\nUse codex exec for non-interactive prompts.\n",
            encoding="utf-8",
        )
        calls: list[dict[str, object]] = []

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
            calls.append({"prompt": prompt, **kwargs})
            return "### Codex Exec\nUse codex exec for non-interactive prompts."

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        context = session_start_hook._select_context_with_ai(
            "parsidion",
            str(tmp_path),
            [note],
            None,
            4000,
            vault_path=tmp_path,
        )

        assert "Codex Exec" in context
        assert calls
        assert calls[0]["model"] is None
        assert calls[0]["model_tier"] == "small"
        assert calls[0]["timeout"] == 7
        assert calls[0]["purpose"] == "session-start-selection"
        assert calls[0]["cwd"] == str(tmp_path)
        assert calls[0]["vault"] == tmp_path
        assert (tmp_path / session_start_hook._AI_STAMP_FILENAME).exists()

    def test_main_no_arg_ai_uses_codex_backend_default_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls = _run_session_start_main_for_codex(monkeypatch, tmp_path, ["--ai"])

        cmd = calls[0]
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
        assert "claude-haiku-4-5-20251001" not in cmd

    def test_main_explicit_ai_model_overrides_codex_backend_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls = _run_session_start_main_for_codex(
            monkeypatch, tmp_path, ["--ai", "custom-codex-model"]
        )

        cmd = calls[0]
        assert cmd[cmd.index("--model") + 1] == "custom-codex-model"

    def test_writes_cooldown_stamp_after_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else 1
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_is_ai_cooldown_active",
            lambda vault_path: False,
        )
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "read_note_summary",
            lambda path, max_lines=6: "Useful summary",
        )

        candidate = tmp_path / "note.md"
        candidate.write_text("ignored", encoding="utf-8")

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
            return "### Note Title (path/to/note.md)\nKey point 1"

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        stamped: list[Path] = []
        monkeypatch.setattr(
            session_start_hook,
            "_write_ai_cooldown_stamp",
            lambda vault_path: stamped.append(vault_path),
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[candidate],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == "### Note Title (path/to/note.md)\nKey point 1"
        assert stamped == [tmp_path]


# ---------------------------------------------------------------------------
# QA-007: AI cooldown helpers
# ---------------------------------------------------------------------------


class TestAiCooldownHelpers:
    """Tests for _is_ai_cooldown_active and _write_ai_cooldown_stamp."""

    def test_cooldown_inactive_when_stamp_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else default
            ),
        )
        # No stamp file — cooldown should not be active
        assert session_start_hook._is_ai_cooldown_active(tmp_path) is False

    def test_cooldown_active_when_stamp_fresh(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else default
            ),
        )
        # Write a fresh stamp
        session_start_hook._write_ai_cooldown_stamp(tmp_path)
        assert session_start_hook._is_ai_cooldown_active(tmp_path) is True

    def test_cooldown_inactive_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                0
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else default
            ),
        )
        # Even with a stamp file, cooldown=0 means always inactive
        session_start_hook._write_ai_cooldown_stamp(tmp_path)
        assert session_start_hook._is_ai_cooldown_active(tmp_path) is False

    def test_write_cooldown_stamp_creates_file(self, tmp_path: Path) -> None:
        stamp = tmp_path / session_start_hook._AI_STAMP_FILENAME
        assert not stamp.exists()
        session_start_hook._write_ai_cooldown_stamp(tmp_path)
        assert stamp.exists()

    def test_write_cooldown_stamp_tolerates_missing_dir(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent"
        # Should not raise even if directory is missing
        session_start_hook._write_ai_cooldown_stamp(nonexistent)


# ---------------------------------------------------------------------------
# QA-007: _build_delta_section
# ---------------------------------------------------------------------------


class TestBuildDeltaSection:
    """Tests for the 'Since last time' delta assembly."""

    def test_returns_empty_when_no_last_seen(self, tmp_path: Path) -> None:
        result = session_start_hook._build_delta_section("parsidion", None, tmp_path)
        assert result == ""

    def test_returns_empty_when_invalid_timestamp(self, tmp_path: Path) -> None:
        result = session_start_hook._build_delta_section(
            "parsidion", "not-a-timestamp", tmp_path
        )
        assert result == ""

    def test_includes_new_notes_after_cutoff(self, tmp_path: Path) -> None:
        # Create vault structure
        vault = tmp_path
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(exist_ok=True)

        # Write a note with a current mtime
        note = vault / "Patterns" / "new-note.md"
        note.write_text("# New Note\n", encoding="utf-8")

        # last_seen is in the past (1970 epoch)
        past_ts = "1970-01-01T00:00:00"
        result = session_start_hook._build_delta_section("parsidion", past_ts, vault)

        assert "new-note" in result
        assert "Since last session" in result

    def test_excludes_notes_before_cutoff(self, tmp_path: Path) -> None:
        vault = tmp_path
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(exist_ok=True)

        note = vault / "Patterns" / "old-note.md"
        note.write_text("# Old Note\n", encoding="utf-8")

        # Set last_seen to the future so the note appears old
        future_ts = "2099-01-01T00:00:00"
        result = session_start_hook._build_delta_section("parsidion", future_ts, vault)

        # No new notes should appear (all are older than the future cutoff)
        assert result == ""

    def test_caps_results_at_ten(self, tmp_path: Path) -> None:
        vault = tmp_path
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(exist_ok=True)

        # Write 15 notes
        for i in range(15):
            note = vault / "Patterns" / f"note-{i:02d}.md"
            note.write_text(f"# Note {i}\n", encoding="utf-8")

        past_ts = "1970-01-01T00:00:00"
        result = session_start_hook._build_delta_section("parsidion", past_ts, vault)

        # Should list at most 10 notes
        new_lines = [line for line in result.splitlines() if "NEW/UPDATED:" in line]
        assert len(new_lines) <= 10


# ---------------------------------------------------------------------------
# Tier 1: graph neighborhood expansion (load_graph_metadata + _graph_neighbors)
# ---------------------------------------------------------------------------


def _use_vault(monkeypatch: pytest.MonkeyPatch, vault: Path) -> None:
    """Point vault_common at *vault* and clear the resolver/config caches."""
    monkeypatch.setattr(session_start_hook.vault_common, "VAULT_ROOT", vault)
    session_start_hook.vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    session_start_hook.vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    session_start_hook.vault_common._clear_config_cache()


def _make_note_index(vault: Path) -> sqlite3.Connection:
    """Create embeddings.db with the canonical note_index schema."""
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    session_start_hook.vault_common.ensure_note_index_schema(conn)
    return conn


def _index_row(
    conn: sqlite3.Connection,
    *,
    stem: str,
    path: Path,
    related: str = "",
    incoming_links: int = 0,
    tags: str = "",
    folder: str = "Patterns",
    title: str = "",
    note_type: str = "pattern",
    project: str = "",
    mtime: float = 1000.0,
) -> None:
    """Insert/replace one row into note_index."""
    conn.execute(
        "INSERT OR REPLACE INTO note_index "
        "(stem, path, folder, title, summary, tags, note_type, project, "
        "confidence, mtime, related, is_stale, incoming_links, date) "
        "VALUES (?, ?, ?, ?, '', ?, ?, ?, '', ?, ?, 0, ?, '')",
        (
            stem,
            str(path),
            folder,
            title or stem,
            tags,
            note_type,
            project,
            mtime,
            related,
            incoming_links,
        ),
    )
    conn.commit()


def _build_meta(
    vault: Path, mapping: dict[str, dict[str, object]]
) -> dict[str, dict[str, object]]:
    """Build an in-memory graph-metadata map and write the note files."""
    meta: dict[str, dict[str, object]] = {}
    for stem, spec in mapping.items():
        path = vault / f"{stem}.md"
        path.write_text(f"# {stem}\n", encoding="utf-8")
        meta[stem] = {
            "path": str(path),
            "related": spec.get("related", ""),
            "incoming_links": spec.get("incoming_links", 0),
            "tags": spec.get("tags", ""),
        }
    return meta


class TestLoadGraphMetadata:
    """Tier 1 data layer: vault_common.load_graph_metadata()."""

    def test_returns_none_when_db_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_vault(monkeypatch, tmp_path)
        assert session_start_hook.vault_common.load_graph_metadata() is None

    def test_returns_none_when_table_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_vault(monkeypatch, tmp_path)
        sqlite3.connect(str(tmp_path / "embeddings.db")).close()
        assert session_start_hook.vault_common.load_graph_metadata() is None

    def test_loads_related_incoming_links_and_tags(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _use_vault(monkeypatch, tmp_path)
        conn = _make_note_index(tmp_path)
        _index_row(
            conn,
            stem="seed",
            path=tmp_path / "seed.md",
            related="nbr-a, nbr-b",
            incoming_links=4,
            tags="python, hook",
        )
        conn.close()
        meta = session_start_hook.vault_common.load_graph_metadata()
        assert meta is not None
        assert meta["seed"]["related"] == "nbr-a, nbr-b"
        assert meta["seed"]["incoming_links"] == 4
        assert "python" in str(meta["seed"]["tags"])
        assert meta["seed"]["path"] == str(tmp_path / "seed.md")


class TestGraphNeighbors:
    """Tier 1 logic: session_start_hook._graph_neighbors (pure, no DB)."""

    def test_returns_outgoing_related_neighbors(self, tmp_path: Path) -> None:
        meta = _build_meta(tmp_path, {"seed": {"related": "nbr-a"}, "nbr-a": {}})
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert [p.stem for p in result] == ["nbr-a"]

    def test_includes_incoming_neighbors(self, tmp_path: Path) -> None:
        # nbr links TO seed, but seed's own related is empty
        meta = _build_meta(
            tmp_path, {"seed": {"related": ""}, "nbr": {"related": "seed"}}
        )
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert "nbr" in [p.stem for p in result]

    def test_excludes_seed_stems(self, tmp_path: Path) -> None:
        meta = _build_meta(tmp_path, {"seed": {"related": "nbr, seed"}, "nbr": {}})
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert "seed" not in [p.stem for p in result]

    def test_caps_at_max_add_keeping_best_connected(self, tmp_path: Path) -> None:
        meta = _build_meta(
            tmp_path,
            {
                "seed": {"related": "a, b, c"},
                "a": {"incoming_links": 1},
                "b": {"incoming_links": 9},
                "c": {"incoming_links": 2},
            },
        )
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=2
        )
        stems = [p.stem for p in result]
        assert len(stems) == 2
        assert "b" in stems  # highest incoming_links survives the cap

    def test_skips_nonexistent_paths(self, tmp_path: Path) -> None:
        meta = {
            "seed": {
                "path": str(tmp_path / "seed.md"),
                "related": "ghost",
                "incoming_links": 0,
                "tags": "",
            },
            "ghost": {
                "path": str(tmp_path / "ghost.md"),
                "related": "",
                "incoming_links": 0,
                "tags": "",
            },
        }
        (tmp_path / "seed.md").write_text("# seed\n", encoding="utf-8")
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert result == []

    def test_skips_paths_outside_vault(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside.md"
        outside.write_text("# outside\n", encoding="utf-8")
        meta = {
            "seed": {
                "path": str(tmp_path / "seed.md"),
                "related": "outside",
                "incoming_links": 0,
                "tags": "",
            },
            "outside": {
                "path": str(outside),
                "related": "",
                "incoming_links": 0,
                "tags": "",
            },
        }
        (tmp_path / "seed.md").write_text("# seed\n", encoding="utf-8")
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert result == []

    def test_returns_empty_when_meta_none(self, tmp_path: Path) -> None:
        (tmp_path / "seed.md").write_text("# seed\n", encoding="utf-8")
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], None, tmp_path, max_add=8
        )
        assert result == []

    def test_dedups_bidirectional_neighbors(self, tmp_path: Path) -> None:
        meta = _build_meta(
            tmp_path, {"seed": {"related": "nbr"}, "nbr": {"related": "seed"}}
        )
        result = session_start_hook._graph_neighbors(
            [tmp_path / "seed.md"], meta, tmp_path, max_add=8
        )
        assert [p.stem for p in result] == ["nbr"]


class TestGraphExpansionIntegration:
    """Tier 1 end-to-end: build_session_context adds 1-hop neighbors."""

    def _setup(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        graph_expand: bool = True,
    ) -> tuple[Path, Path, Path]:
        vault = tmp_path / "vault"
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(parents=True, exist_ok=True)
        seed = vault / "Patterns" / "seed.md"
        nbr = vault / "Patterns" / "neighbor.md"
        seed.write_text(
            '---\ntype: pattern\ntags: [python]\nrelated: ["[[neighbor]]"]\n---\n# Seed\n',
            encoding="utf-8",
        )
        nbr.write_text(
            "---\ntype: pattern\ntags: [python]\n---\n# Neighbor\n", encoding="utf-8"
        )
        conn = _make_note_index(vault)
        _index_row(
            conn,
            stem="seed",
            path=seed,
            related="neighbor",
            incoming_links=1,
            tags="python",
        )
        _index_row(
            conn,
            stem="neighbor",
            path=nbr,
            related="seed",
            incoming_links=1,
            tags="python",
        )
        conn.close()
        (vault / "config.yaml").write_text(
            "session_start_hook:\n"
            "  use_embeddings: false\n"
            "  track_delta: false\n"
            f"  graph_expand: {str(graph_expand).lower()}\n"
            "  graph_rerank: false\n",
            encoding="utf-8",
        )
        _use_vault(monkeypatch, vault)
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "find_notes_by_project",
            lambda project: [seed],
        )
        monkeypatch.setattr(
            session_start_hook.vault_common, "find_recent_notes", lambda days=3: []
        )
        monkeypatch.setattr(
            session_start_hook, "_run_semantic_search", lambda *a, **k: []
        )
        return vault, seed, nbr

    def test_expansion_adds_neighbor_to_context(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, _seed, _nbr = self._setup(monkeypatch, tmp_path)
        context, _count = session_start_hook.build_session_context(cwd=str(vault))
        assert "neighbor" in context

    def test_disabled_does_not_expand(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, _seed, _nbr = self._setup(monkeypatch, tmp_path, graph_expand=False)
        context, _count = session_start_hook.build_session_context(cwd=str(vault))
        assert "neighbor" not in context


# ---------------------------------------------------------------------------
# Tier 2: graph-aware rerank (_rank_by_graph)
# ---------------------------------------------------------------------------


class TestGraphRerank:
    """Tier 2 logic: session_start_hook._rank_by_graph (pure, no DB)."""

    def test_boosts_tag_overlap_above_no_overlap(self, tmp_path: Path) -> None:
        meta = _build_meta(
            tmp_path,
            {
                "seed": {"tags": "python"},
                "shares": {"tags": "python"},
                "other": {"tags": "rust"},
            },
        )
        notes = [tmp_path / "other.md", tmp_path / "shares.md", tmp_path / "seed.md"]
        seed = [tmp_path / "seed.md"]
        result = session_start_hook._rank_by_graph(notes, seed, meta)
        stems = [p.stem for p in result]
        assert stems.index("shares") < stems.index("other")
        assert stems.index("seed") < stems.index("other")

    def test_hubness_breaks_tag_ties(self, tmp_path: Path) -> None:
        meta = _build_meta(
            tmp_path,
            {
                "seed": {"tags": "python"},
                "a": {"tags": "python", "incoming_links": 1},
                "b": {"tags": "python", "incoming_links": 9},
            },
        )
        notes = [tmp_path / "a.md", tmp_path / "b.md", tmp_path / "seed.md"]
        seed = [tmp_path / "seed.md"]
        result = session_start_hook._rank_by_graph(notes, seed, meta)
        assert [p.stem for p in result][0] == "b"

    def test_stable_for_ties_preserves_input_order(self, tmp_path: Path) -> None:
        meta = _build_meta(
            tmp_path,
            {
                "seed": {"tags": "python"},
                "a": {"tags": "python"},
                "b": {"tags": "python"},
                "c": {"tags": "python"},
            },
        )
        notes = [
            tmp_path / "a.md",
            tmp_path / "b.md",
            tmp_path / "c.md",
            tmp_path / "seed.md",
        ]
        seed = [tmp_path / "seed.md"]
        result = session_start_hook._rank_by_graph(notes, seed, meta)
        # All share the python tag with the cluster and have equal hubness ->
        # stable sort preserves original relative order.
        assert [p.stem for p in result] == ["a", "b", "c", "seed"]

    def test_cluster_tags_come_from_seeds_not_neighbors(self, tmp_path: Path) -> None:
        # nbr carries tag "go" (not in any seed); m carries "python" (in seed).
        # Cluster must be derived from seed_paths only, so m outranks nbr.
        meta = _build_meta(
            tmp_path,
            {
                "seed": {"tags": "python"},
                "nbr": {"tags": "go"},
                "m": {"tags": "python"},
            },
        )
        notes = [tmp_path / "nbr.md", tmp_path / "m.md", tmp_path / "seed.md"]
        seed = [tmp_path / "seed.md"]
        result = session_start_hook._rank_by_graph(notes, seed, meta)
        stems = [p.stem for p in result]
        assert stems.index("m") < stems.index("nbr")

    def test_none_meta_returns_notes_unchanged(self, tmp_path: Path) -> None:
        notes = [tmp_path / "a.md", tmp_path / "b.md"]
        for path in notes:
            path.write_text("# x\n", encoding="utf-8")
        result = session_start_hook._rank_by_graph(notes, [tmp_path / "a.md"], None)
        assert result == notes

    def test_empty_notes_returns_empty(self, tmp_path: Path) -> None:
        assert session_start_hook._rank_by_graph([], [], {}) == []


class TestGraphRerankIntegration:
    """Tier 2 end-to-end: rerank overrides hubness when tag overlap differs."""

    def test_rerank_orders_tag_matching_neighbor_first(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault = tmp_path / "vault"
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(parents=True, exist_ok=True)
        seed = vault / "Patterns" / "seed.md"
        nbr_a = vault / "Patterns" / "nbr-a.md"  # shares python tag
        nbr_b = vault / "Patterns" / "nbr-b.md"  # tag "go", but far better connected
        seed.write_text(
            '---\ntype: pattern\ntags: [python]\nrelated: ["[[nbr-a]]", "[[nbr-b]]"]\n---\n# Seed\n',
            encoding="utf-8",
        )
        nbr_a.write_text(
            "---\ntype: pattern\ntags: [python]\n---\n# NbrA\n", encoding="utf-8"
        )
        nbr_b.write_text(
            "---\ntype: pattern\ntags: [go]\n---\n# NbrB\n", encoding="utf-8"
        )
        conn = _make_note_index(vault)
        _index_row(
            conn,
            stem="seed",
            path=seed,
            related="nbr-a, nbr-b",
            incoming_links=2,
            tags="python",
        )
        _index_row(
            conn,
            stem="nbr-a",
            path=nbr_a,
            related="seed",
            incoming_links=1,
            tags="python",
        )
        _index_row(
            conn, stem="nbr-b", path=nbr_b, related="seed", incoming_links=9, tags="go"
        )
        conn.close()
        (vault / "config.yaml").write_text(
            "session_start_hook:\n"
            "  use_embeddings: false\n"
            "  track_delta: false\n"
            "  graph_expand: true\n"
            "  graph_expand_max: 8\n"
            "  graph_rerank: true\n",
            encoding="utf-8",
        )
        _use_vault(monkeypatch, vault)
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "find_notes_by_project",
            lambda project: [seed],
        )
        monkeypatch.setattr(
            session_start_hook.vault_common, "find_recent_notes", lambda days=3: []
        )
        monkeypatch.setattr(
            session_start_hook, "_run_semantic_search", lambda *a, **k: []
        )

        context, _count = session_start_hook.build_session_context(cwd=str(vault))
        # nbr-b is more connected (would lead under pure hubness) but nbr-a
        # shares the python tag, so rerank must place nbr-a before nbr-b.
        assert context.index("[[nbr-a]]") < context.index("[[nbr-b]]")


# ---------------------------------------------------------------------------
# Phase 3: graph expansion in AI-selection mode (_build_candidates enrichment)
# ---------------------------------------------------------------------------


def _setup_graph_vault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path, Path, Path]:
    """Build a vault + note_index with a project note, a graph neighbor, and a
    recent note. The neighbor is neither recent nor project-scoped, so it only
    surfaces via graph expansion."""
    vault = tmp_path / "vault"
    for d in session_start_hook.vault_common.VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    proj = vault / "Projects" / "proj-note.md"
    nbr = vault / "Projects" / "neighbor.md"
    recent = vault / "Patterns" / "recent-note.md"
    for path, body in (
        (proj, "# Proj\n"),
        (nbr, "# Neighbor\n"),
        (recent, "# Recent\n"),
    ):
        path.write_text(body, encoding="utf-8")
    conn = _make_note_index(vault)
    _index_row(
        conn,
        stem="proj-note",
        path=proj,
        project="vault",
        related="neighbor",
        tags="python",
        mtime=2_000_000_000.0,
    )
    _index_row(
        conn,
        stem="neighbor",
        path=nbr,
        project="",
        related="proj-note",
        tags="python",
        mtime=1000.0,  # old -> not a recent note
    )
    _index_row(
        conn,
        stem="recent-note",
        path=recent,
        project="",
        related="",
        tags="rust",
        mtime=2_000_000_000.0,
    )
    conn.close()
    _use_vault(monkeypatch, vault)
    return vault, proj, nbr, recent


class TestBuildCandidatesGraphEnrichment:
    """Phase 3 logic: _build_candidates(graph_meta=..., graph_expand_max=...)."""

    def test_includes_neighbor_after_project_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, _proj, _nbr, _recent = _setup_graph_vault(monkeypatch, tmp_path)
        meta = session_start_hook.vault_common.load_graph_metadata()
        result = session_start_hook._build_candidates(
            "vault", vault, graph_meta=meta, graph_expand_max=8
        )
        stems = [p.stem for p in result]
        assert "neighbor" in stems
        # Inserted between the project note and the recent note.
        assert stems.index("proj-note") < stems.index("neighbor")
        assert stems.index("neighbor") < stems.index("recent-note")

    def test_skips_when_meta_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, _proj, _nbr, _recent = _setup_graph_vault(monkeypatch, tmp_path)
        result = session_start_hook._build_candidates(
            "vault", vault, graph_meta=None, graph_expand_max=8
        )
        assert "neighbor" not in [p.stem for p in result]

    def test_skips_when_max_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, _proj, _nbr, _recent = _setup_graph_vault(monkeypatch, tmp_path)
        meta = session_start_hook.vault_common.load_graph_metadata()
        result = session_start_hook._build_candidates(
            "vault", vault, graph_meta=meta, graph_expand_max=0
        )
        assert "neighbor" not in [p.stem for p in result]

    def test_dedups_neighbor_already_in_base(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # neighbor is also a recent note here -> already in the base list; it
        # must not be duplicated by enrichment.
        vault = tmp_path / "vault"
        for d in session_start_hook.vault_common.VAULT_DIRS:
            (vault / d).mkdir(parents=True, exist_ok=True)
        proj = vault / "Projects" / "proj-note.md"
        nbr = vault / "Projects" / "neighbor.md"
        proj.write_text("# Proj\n", encoding="utf-8")
        nbr.write_text("# Neighbor\n", encoding="utf-8")
        conn = _make_note_index(vault)
        _index_row(
            conn,
            stem="proj-note",
            path=proj,
            project="vault",
            related="neighbor",
            tags="python",
            mtime=2_000_000_000.0,
        )
        _index_row(
            conn,
            stem="neighbor",
            path=nbr,
            project="",
            related="proj-note",
            tags="python",
            mtime=2_000_000_000.0,  # recent -> already in base
        )
        conn.close()
        _use_vault(monkeypatch, vault)
        meta = session_start_hook.vault_common.load_graph_metadata()
        result = session_start_hook._build_candidates(
            "vault", vault, graph_meta=meta, graph_expand_max=8
        )
        stems = [p.stem for p in result]
        assert stems.count("neighbor") == 1


class TestAiBranchGraphEnrichment:
    """Phase 3 wiring: the AI branch feeds graph neighbors into the selector."""

    def _setup_with_config(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        *,
        graph_expand: bool,
    ) -> tuple[Path, dict[str, list[str]]]:
        vault, _proj, _nbr, _recent = _setup_graph_vault(monkeypatch, tmp_path)
        (vault / "config.yaml").write_text(
            "session_start_hook:\n"
            f"  graph_expand: {str(graph_expand).lower()}\n"
            "  graph_expand_max: 8\n"
            "  track_delta: false\n",
            encoding="utf-8",
        )
        session_start_hook.vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
        session_start_hook.vault_common._clear_config_cache()
        captured: dict[str, list[str]] = {}

        def fake_select(
            project_name: str,
            cwd: str,
            candidate_notes: list[Path],
            model: object,
            max_chars: int,
            vault_path: Path | None = None,
        ) -> str:
            captured["stems"] = [p.stem for p in candidate_notes]
            return "### Neighbor\ngraph context"  # non-empty -> AI path returns

        monkeypatch.setattr(session_start_hook, "_select_context_with_ai", fake_select)
        return vault, captured

    def test_ai_branch_passes_graph_neighbors_to_selector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, captured = self._setup_with_config(
            monkeypatch, tmp_path, graph_expand=True
        )
        session_start_hook.build_session_context(
            cwd=str(vault), ai_model="some-model", ai_enabled=True
        )
        assert "neighbor" in captured["stems"]

    def test_ai_branch_skips_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault, captured = self._setup_with_config(
            monkeypatch, tmp_path, graph_expand=False
        )
        session_start_hook.build_session_context(
            cwd=str(vault), ai_model="some-model", ai_enabled=True
        )
        assert "neighbor" not in captured["stems"]
