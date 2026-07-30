"""Regression tests for vault_doctor.py mutation-safety fixes.

Covers:
- Block-sequence underscore normalization must not corrupt later
  block-sequence fields (e.g. ``sources:`` URLs with underscores).
- ``run_migrate_subfolders`` must respect the generic-word cluster filter
  (``_filter_clusters_with_claude``) in both dry-run and execute modes, and
  must skip unvetted clusters when the AI backend is unavailable.
- ``run_strip_prefixes`` must continue past a failing rename and only patch
  wikilinks for stems that actually renamed.
"""

from pathlib import Path

import pytest

import vault_common
import vault_doctor


@pytest.fixture(autouse=True)
def _patch_vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wire resolve_vault() to a fresh tmp dir and point vault_doctor there."""
    monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)


@pytest.fixture()
def vault(tmp_vault: Path) -> Path:
    """Return the tmp vault path and create standard dirs."""
    for d in vault_common.VAULT_DIRS:
        (tmp_vault / d).mkdir(exist_ok=True)
    return tmp_vault


def _write_note(vault: Path, rel_path: str, content: str) -> Path:
    """Helper: write a note file and return its Path."""
    full = vault / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


# ---------------------------------------------------------------------------
# Underscore normalization — block-sequence bounding
# ---------------------------------------------------------------------------


class TestNormalizeUnderscoresBlockBounding:
    def test_sources_block_urls_untouched_while_tags_fixed(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            "tags:\n"
            "  - par_ai_core\n"
            "  - vault\n"
            "sources:\n"
            "  - https://example.com/some_page_with_underscores\n"
            "  - https://example.com/other_doc\n"
            'related: ["[[note-a]]"]\n'
            "---\n\n# Test\n"
        )
        note = _write_note(vault, "Patterns/block-tags.md", content)

        modified = vault_doctor._normalize_underscores_in_frontmatter(
            [note], dry_run=False, vault_path=vault
        )

        assert modified == 1
        updated = note.read_text(encoding="utf-8")
        assert "  - par-ai-core\n" in updated
        # Later block-sequence fields must be left byte-identical
        assert "  - https://example.com/some_page_with_underscores\n" in updated
        assert "  - https://example.com/other_doc\n" in updated
        assert "par_ai_core" not in updated

    def test_tags_block_ending_at_blank_line(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            "tags:\n"
            "  - my_tag\n"
            "\n"
            "sources:\n"
            "  - https://example.com/under_score\n"
            "---\n\n# Test\n"
        )
        note = _write_note(vault, "Patterns/blank-sep.md", content)

        modified = vault_doctor._normalize_underscores_in_frontmatter(
            [note], dry_run=False, vault_path=vault
        )

        assert modified == 1
        updated = note.read_text(encoding="utf-8")
        assert "  - my-tag\n" in updated
        assert "  - https://example.com/under_score\n" in updated


# ---------------------------------------------------------------------------
# Migrate-subfolders — generic-word cluster filtering
# ---------------------------------------------------------------------------


def _make_generic_cluster(vault: Path) -> list[Path]:
    """Create three Debugging notes sharing the generic prefix 'fixing'."""
    return [
        _write_note(vault, f"Debugging/fixing-{s}.md", f"# Fixing {s}\n")
        for s in ("alpha", "beta", "gamma")
    ]


class TestMigrateSubfoldersFilter:
    def test_execute_respects_filter_rejection(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notes = _make_generic_cluster(vault)

        monkeypatch.setattr(
            vault_doctor, "_filter_clusters_with_claude", lambda clusters, **kw: []
        )

        vault_doctor.run_migrate_subfolders(vault, dry_run=False)

        for note in notes:
            assert note.exists(), f"{note.name} must not move when filter rejects"
        assert not (vault / "Debugging" / "fixing").exists()

    def test_dry_run_applies_same_filter(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _make_generic_cluster(vault)
        calls: list[list[tuple[Path, str, list[Path], Path | None]]] = []

        def fake_filter(
            clusters: list[tuple[Path, str, list[Path], Path | None]],
            **kw: object,
        ) -> list[tuple[Path, str, list[Path], Path | None]]:
            calls.append(clusters)
            return []

        monkeypatch.setattr(vault_doctor, "_filter_clusters_with_claude", fake_filter)

        vault_doctor.run_migrate_subfolders(vault, dry_run=True)

        assert len(calls) == 1, "dry-run must apply the filter so previews match"
        assert "No subfolder migration candidates found." in capsys.readouterr().out

    def test_execute_moves_when_filter_keeps(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notes = _make_generic_cluster(vault)

        monkeypatch.setattr(
            vault_doctor,
            "_filter_clusters_with_claude",
            lambda clusters, **kw: clusters,
        )
        # Avoid spawning the real update_index.py subprocess
        monkeypatch.setattr(vault_doctor.subprocess, "run", lambda *a, **kw: None)

        vault_doctor.run_migrate_subfolders(vault, dry_run=False)

        for note in notes:
            assert not note.exists()
        subfolder = vault / "Debugging" / "fixing"
        assert sorted(p.name for p in subfolder.glob("*.md")) == [
            "alpha.md",
            "beta.md",
            "gamma.md",
        ]

    def test_ai_unavailable_skips_clusters(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        notes = _make_generic_cluster(vault)

        # AI backend unavailable → run_ai_prompt returns None
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )

        vault_doctor.run_migrate_subfolders(vault, dry_run=False)

        for note in notes:
            assert note.exists(), "unvetted clusters must not move without AI vetting"
        assert not (vault / "Debugging" / "fixing").exists()
        assert "AI backend unavailable" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Strip-prefixes — resilience to failing renames
# ---------------------------------------------------------------------------


class TestStripPrefixesRenameFailure:
    def test_continues_past_failing_rename(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_note(vault, "Projects/myapp/myapp-overview.md", "# Overview\n")
        _write_note(vault, "Projects/myapp/myapp-details.md", "# Details\n")
        linker = _write_note(
            vault,
            "Patterns/linker.md",
            "# Linker\n\nSee [[myapp-overview]] and [[myapp-details]].\n",
        )

        real_rename = Path.rename

        def flaky_rename(self: Path, target: object) -> Path:
            if self.name == "myapp-details.md":
                raise OSError("disk full")
            return real_rename(self, target)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "rename", flaky_rename)

        vault_doctor.run_strip_prefixes(
            dry_run=False, vault_path=vault, auto_reindex=False
        )

        myapp = vault / "Projects" / "myapp"
        assert (myapp / "overview.md").exists()
        assert (myapp / "myapp-details.md").exists()  # failed rename left in place
        assert not (myapp / "details.md").exists()

        body = linker.read_text(encoding="utf-8")
        assert "[[overview]]" in body
        # Wikilinks for the failed rename must NOT be patched
        assert "[[myapp-details]]" in body
        assert "[[details]]" not in body.replace("[[myapp-details]]", "")

        assert "rename failed" in capsys.readouterr().err

    def test_all_renames_failing_reports_and_returns(
        self,
        vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _write_note(vault, "Projects/myapp/myapp-overview.md", "# Overview\n")
        _write_note(vault, "Projects/myapp/myapp-details.md", "# Details\n")

        def always_fail(self: Path, target: object) -> Path:
            raise OSError("read-only filesystem")

        monkeypatch.setattr(Path, "rename", always_fail)

        vault_doctor.run_strip_prefixes(
            dry_run=False, vault_path=vault, auto_reindex=False
        )

        captured = capsys.readouterr()
        assert "No files were renamed." in captured.out
        assert captured.err.count("rename failed") == 2


# ---------------------------------------------------------------------------
# SEC-109/110/112/114 — vault_doctor.run_fix_permissions migration
# ---------------------------------------------------------------------------


class TestRunFixPermissions:
    """Permission-repair migration in vault_doctor --fix-all (and standalone
    via --fix-permissions). Closes SEC-109/110/112/114 for pre-existing
    files that were created at the umask default before those fixes landed.
    """

    def test_chmods_secret_files_to_0600(self, vault: Path) -> None:
        import os
        import stat

        # Pre-fix state: pending_summaries.jsonl and config.yaml exist at
        # 0644 (the umask default).
        pending = vault / "pending_summaries.jsonl"
        pending.write_text("{}\n", encoding="utf-8")
        os.chmod(pending, 0o644)
        config = vault / "config.yaml"
        config.write_text("vault:\n  username: x\n", encoding="utf-8")
        os.chmod(config, 0o644)

        repaired = vault_doctor.run_fix_permissions(vault)
        assert repaired >= 1

        assert stat.S_IMODE(os.stat(pending).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(config).st_mode) == 0o600

    def test_chmods_dead_letter_and_bak_variants(self, vault: Path) -> None:
        import os
        import stat

        dead = vault / "dead_letters.jsonl"
        dead.write_text("[]\n", encoding="utf-8")
        os.chmod(dead, 0o644)
        bak = vault / "dead_letters.jsonl.bak1"
        bak.write_text("[]\n", encoding="utf-8")
        os.chmod(bak, 0o644)

        vault_doctor.run_fix_permissions(vault)

        assert stat.S_IMODE(os.stat(dead).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(bak).st_mode) == 0o600

    def test_chmods_vault_root_to_0700(self, vault: Path) -> None:
        import os
        import stat

        os.chmod(vault, 0o755)
        vault_doctor.run_fix_permissions(vault)
        assert stat.S_IMODE(os.stat(vault).st_mode) == 0o700

    def test_dry_run_does_not_chmod(self, vault: Path) -> None:
        import os
        import stat

        pending = vault / "pending_summaries.jsonl"
        pending.write_text("{}\n", encoding="utf-8")
        os.chmod(pending, 0o644)

        repaired = vault_doctor.run_fix_permissions(vault, dry_run=True)
        assert repaired == 0
        # Mode unchanged
        assert stat.S_IMODE(os.stat(pending).st_mode) == 0o644

    def test_missing_files_are_skipped_silently(self, vault: Path) -> None:
        # No pending_summaries.jsonl, no config.yaml — should not raise.
        result = vault_doctor.run_fix_permissions(vault)
        # Only the vault root exists for sure; result is at least 0.
        assert isinstance(result, int)
        assert result >= 0

    def test_chmod_failure_does_not_raise(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing chmod must not abort the whole run."""

        def boom(self: Path, mode: int) -> None:  # noqa: ARG001
            raise OSError("permission denied")

        pending = vault / "pending_summaries.jsonl"
        pending.write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(Path, "chmod", boom)
        # Should not raise
        vault_doctor.run_fix_permissions(vault)


# ---------------------------------------------------------------------------
# SEC-128 — `--` before note-derived positionals in vault-search subprocess
# ---------------------------------------------------------------------------


class TestVaultSearchSeparator:
    """SEC-128: insert `--` before note-derived positionals so a wikilink or
    H1 like ``[[--help]]`` cannot parse as a vault-search flag."""

    def test_find_link_recovery_passes_separator(self, vault: Path) -> None:
        """Verify the argv passed to subprocess.run carries a `--` separator
        before the user-derived positional."""
        captured_argv: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> object:
            captured_argv.append(argv)

            # Simulate vault-search returning no results
            class _FakeCompleted:
                returncode = 0
                stdout = "[]"

            return _FakeCompleted()

        original_run = vault_doctor.subprocess.run
        vault_doctor.subprocess.run = fake_run  # type: ignore[assignment]

        try:
            vault_doctor._find_link_replacement(
                "[[--help]]",
                {},
                exclude_path=None,
                min_score=0.5,
            )
        finally:
            vault_doctor.subprocess.run = original_run  # type: ignore[assignment]

        assert captured_argv, "subprocess.run was not invoked"
        argv = captured_argv[0]
        # The `--` must be present and the user-derived positional must be
        # AFTER it (so it cannot be parsed as a flag).
        assert "--" in argv
        separator_idx = argv.index("--")
        assert argv[separator_idx + 1 :] == ["[[--help]]"]
