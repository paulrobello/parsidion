"""Skeleton tests for update_index.py.

ARC-017: These tests cover the core utility functions that can be tested
without a live vault.  Full integration tests (index build, MANIFEST generation,
DB writes) require a populated vault and are left as future work.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import update_index


class TestExtractTitle:
    """Tests for update_index._extract_title (delegates to vault_common.extract_title)."""

    def test_heading_extracted(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# My Note Title\nBody.\n"
        assert update_index._extract_title(content, "my-note-title") == "My Note Title"

    def test_fallback_to_stem(self) -> None:
        content = "---\ndate: 2026-01-01\n---\nNo heading here.\n"
        assert update_index._extract_title(content, "my-note-title") == "My Note Title"

    def test_double_hash_not_matched(self) -> None:
        """A ## heading should not be used as the note title."""
        content = "---\ndate: 2026-01-01\n---\n\n## Section Heading\nBody.\n"
        result = update_index._extract_title(content, "my-note")
        assert result == "My Note"


class TestExtractSummary:
    """Tests for update_index._extract_summary."""

    def test_first_body_line(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# Title\nThis is the summary.\n"
        assert update_index._extract_summary(content) == "This is the summary."

    def test_skips_headings(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# Title\n## Section\nActual content.\n"
        assert update_index._extract_summary(content) == "Actual content."

    def test_skips_comments(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n<!-- comment -->\nReal line.\n"
        assert update_index._extract_summary(content) == "Real line."

    def test_truncation(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n" + "x" * 200 + "\n"
        result = update_index._extract_summary(content)
        assert len(result) <= update_index.SUMMARY_MAX_CHARS
        assert result.endswith("...")

    def test_empty_body(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n"
        assert update_index._extract_summary(content) == ""


class TestFolderName:
    """Tests for update_index._folder_name.

    ARC-003: ``_folder_name`` now takes the vault path as an explicit
    argument rather than reading the module-level ``VAULT_ROOT`` constant,
    so the previous patch-both-modules dance is no longer required.
    """

    def test_direct_vault_child(self, tmp_path: Path) -> None:
        note = tmp_path / "Patterns" / "my-note.md"
        result = update_index._folder_name(note, tmp_path)
        assert result == "Patterns"

    def test_root_level_note(self, tmp_path: Path) -> None:
        note = tmp_path / "CLAUDE.md"
        result = update_index._folder_name(note, tmp_path)
        assert result == ""


class TestExtractWikilinkStems:
    """Tests for update_index._extract_wikilink_stems."""

    def test_inline_wikilinks(self) -> None:
        related = ["[[note-a]]", "[[note-b]]"]
        stems = update_index._extract_wikilink_stems(related)
        assert stems == ["note-a", "note-b"]

    def test_bare_strings(self) -> None:
        stems = update_index._extract_wikilink_stems(["bare-string"])
        assert stems == ["bare-string"]

    def test_non_list(self) -> None:
        assert update_index._extract_wikilink_stems("not-a-list") == []  # type: ignore[arg-type]

    def test_empty_list(self) -> None:
        assert update_index._extract_wikilink_stems([]) == []

    def test_mixed(self) -> None:
        related = ["[[note-a]]", "bare", 42]  # type: ignore[list-item]
        stems = update_index._extract_wikilink_stems(related)
        assert "note-a" in stems
        assert "bare" in stems


class TestGraphIncrementalFlag:
    """ENH-010: --graph-incremental is a tri-state so the default (defer to
    config, now on) can be overridden either way from the CLI."""

    def test_default_defers_to_config(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sys

        monkeypatch.setattr(sys, "argv", ["update_index"])
        assert update_index._parse_args().graph_incremental is None

    def test_explicit_enable(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import sys

        monkeypatch.setattr(sys, "argv", ["update_index", "--graph-incremental"])
        assert update_index._parse_args().graph_incremental is True

    def test_explicit_disable(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        """--no-graph-incremental must force a full rebuild (the opt-out)."""
        import sys

        monkeypatch.setattr(sys, "argv", ["update_index", "--no-graph-incremental"])
        assert update_index._parse_args().graph_incremental is False


class TestStaticOnlySuppressesModelWork:
    """PARSIDION_STATIC_ONLY=1 must propagate through ``update_index.main()``.

    The v3 vault post-merge hook skips its own ``build_embeddings.py`` step
    under PARSIDION_STATIC_ONLY=1, but ``update_index.py`` independently
    spawns ``build_embeddings.py`` when ``embeddings.enabled`` is true, and a
    parsight background index when the parsight backend resolves. Static-only
    semantics require suppressing both, while the metadata rebuild
    (CLAUDE.md / TAGS.md / MANIFEST.md / note_index table) still runs.
    """

    def _prepare_vault(self, vault: Path) -> None:
        (vault / "Patterns").mkdir(parents=True, exist_ok=True)
        (vault / "Patterns" / "static-only-note.md").write_text(
            "---\ndate: 2026-10-25\ntype: pattern\ntags: [hook]\n"
            'related: ["[[other-note]]"]\n---\n\n# Static Only Note\nBody.\n',
            encoding="utf-8",
        )
        # embeddings explicitly ON: proves suppression comes from the env var,
        # not from the embeddings.enabled config gate. Pre-create embeddings.db
        # so the note_index metadata upsert runs (it no-ops on a missing DB).
        (vault / "config.yaml").write_text(
            "embeddings:\n  enabled: true\n", encoding="utf-8"
        )
        (vault / "embeddings.db").touch()

    def _stub_spawns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        calls: dict[str, int],
        cmds: list[list[str]],
    ) -> None:
        class _Proc:
            returncode = 0

            @staticmethod
            def poll() -> None:
                return None  # pretend the child launched and is running

        def fake_popen(*args: object, **kwargs: object) -> _Proc:
            calls["popen"] = calls.get("popen", 0) + 1
            cmd = args[0] if args else kwargs.get("args")
            assert isinstance(cmd, list)
            cmds.append([str(a) for a in cmd])
            return _Proc()

        monkeypatch.setattr(
            update_index,
            "subprocess",
            SimpleNamespace(Popen=fake_popen, DEVNULL=-3),
        )

        import parsight_backend

        monkeypatch.setattr(
            parsight_backend,
            "resolve_parsight_backend",
            lambda vault_path: (
                calls.__setitem__(
                    "parsight_resolve", calls.get("parsight_resolve", 0) + 1
                )
                or True
            ),
        )
        monkeypatch.setattr(
            parsight_backend, "daemon_watches_vault", lambda vault_path: False
        )

        def fake_spawn(vault_path: object) -> bool:
            calls["parsight_spawn"] = calls.get("parsight_spawn", 0) + 1
            return True

        monkeypatch.setattr(parsight_backend, "spawn_background_index", fake_spawn)

    def test_static_only_skips_model_spawns_with_embeddings_enabled(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import sqlite3
        import sys

        self._prepare_vault(tmp_vault)
        monkeypatch.setenv("HOME", str(tmp_vault / "home"))
        calls: dict[str, int] = {}
        cmds: list[list[str]] = []
        self._stub_spawns(monkeypatch, calls, cmds)
        monkeypatch.setattr(sys, "argv", ["update_index"])
        monkeypatch.setenv("PARSIDION_STATIC_ONLY", "1")

        update_index.main()

        assert calls.get("popen", 0) == 0, cmds
        assert calls.get("parsight_spawn", 0) == 0
        # The skip happens before the parsight availability probe.
        assert calls.get("parsight_resolve", 0) == 0
        assert "PARSIDION_STATIC_ONLY=1" in capsys.readouterr().out
        # Metadata rebuild preserved: generated index files and note_index rows.
        assert (tmp_vault / "CLAUDE.md").exists()
        assert (tmp_vault / "TAGS.md").exists()
        assert (tmp_vault / "Patterns" / "MANIFEST.md").exists()
        con = sqlite3.connect(tmp_vault / "embeddings.db")
        try:
            count = con.execute("SELECT COUNT(*) FROM note_index").fetchone()[0]
        finally:
            con.close()
        assert count == 1

    def test_default_still_spawns_with_embeddings_enabled(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import sys

        self._prepare_vault(tmp_vault)
        monkeypatch.setenv("HOME", str(tmp_vault / "home"))
        calls: dict[str, int] = {}
        cmds: list[list[str]] = []
        self._stub_spawns(monkeypatch, calls, cmds)
        monkeypatch.setattr(sys, "argv", ["update_index"])
        monkeypatch.delenv("PARSIDION_STATIC_ONLY", raising=False)

        update_index.main()

        assert calls.get("popen", 0) == 1, cmds
        assert any("build_embeddings.py" in part for part in cmds[0]), cmds
        assert "--no-project" in cmds[0]
        assert calls.get("parsight_spawn", 0) == 1
        assert "Embeddings: " in capsys.readouterr().out
