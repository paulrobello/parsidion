"""Regression tests for SEC-101 (vault post-merge hook RCE) and SEC-104 (vault
``.gitignore`` globs + substring-membership bug).

These pin two security invariants:

* every ``uv run`` line in the installed hook template carries ``--no-project``
  (SEC-101 — a missing flag grants RCE to a hostile vault remote via PEP 517
  build-backend discovery in the vault worktree).
* the vault ``.gitignore`` covers backup variants of sensitive files
  (SEC-104 — exact-filename entries let ``pending_summaries.jsonl.bak`` slip
  through), and a commented-out ``# config.yaml`` no longer suppresses the real
  entry (SEC-104 substring-comparison bug).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from installer.vault import (
    _POST_MERGE_HOOK_TEMPLATE,
    _POST_MERGE_LEGACY_MARKERS,
    _POST_MERGE_MARKER,
    _is_current_post_merge_hook,
    configure_vault_gitignore,
    install_vault_post_merge_hook,
    remove_vault_post_merge_hook,
)


class TestTemplateInvariants:
    """SEC-101: the template must not regress the ``--no-project`` fix."""

    def test_every_uv_run_line_carries_no_project(self) -> None:
        rendered = _POST_MERGE_HOOK_TEMPLATE.format(
            marker=_POST_MERGE_MARKER,
            update_index_script="/opt/scripts/update_index.py",
            build_embeddings_script="/opt/scripts/build_embeddings.py",
        )
        uv_run_lines = [
            ln for ln in rendered.splitlines() if ln.lstrip().startswith("uv run")
        ]
        # Sanity: we have at least two uv run lines (the two we ship today).
        assert len(uv_run_lines) >= 2, rendered
        assert all("--no-project" in ln for ln in uv_run_lines), rendered

    def test_legacy_markers_listed(self) -> None:
        # The pre-rename marker string must be recognised so stale hooks
        # regenerate on the next install instead of being skipped.
        assert "# parsidion-cc post-merge hook" in _POST_MERGE_LEGACY_MARKERS


class TestIsCurrentPostMergeHook:
    """SEC-101: marker check distinguishes current, stale-ours, and not-ours."""

    def _render(self, *, body_uv_lines: list[str]) -> str:
        lines = [
            "#!/bin/bash",
            f"{_POST_MERGE_MARKER} — rebuilds vault index and embeddings after pull",
            "set -e",
        ]
        lines.extend(body_uv_lines)
        return "\n".join(lines) + "\n"

    def test_current_marker_with_no_project_is_current(self) -> None:
        body = [
            "uv run --no-project ~/scripts/update_index.py",
            "uv run --no-project ~/scripts/build_embeddings.py --incremental",
        ]
        assert _is_current_post_merge_hook(self._render(body_uv_lines=body))

    def test_current_marker_missing_no_project_is_stale(self) -> None:
        # This is the SEC-101 defect — must NOT be reported as current.
        body = [
            "uv run --no-project ~/scripts/update_index.py",
            "uv run ~/scripts/build_embeddings.py --incremental",  # missing flag
        ]
        assert not _is_current_post_merge_hook(self._render(body_uv_lines=body))

    def test_legacy_marker_is_not_current(self) -> None:
        # Carries the parsidion-cc legacy marker, not the current one — must
        # not be reported as current (so install regenerates it).
        legacy = "#!/bin/bash\n# parsidion-cc post-merge hook\nset -e\nuv run x\n"
        assert not _is_current_post_merge_hook(legacy)

    def test_foreign_hook_is_not_current(self) -> None:
        foreign = "#!/bin/bash\necho hello\n"
        assert not _is_current_post_merge_hook(foreign)


class TestInstallPostMergeHookRegeneratesLegacy:
    """SEC-101: a stale parsidion-cc hook is regenerated, not skipped."""

    def test_legacy_marker_hook_is_overwritten(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        vault = tmp_path / "vault"
        hooks_dir = vault / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_path = hooks_dir / "post-merge"

        # Simulate the live stale state on this machine: legacy marker plus
        # dead parsidion-cc script paths.
        legacy_body = (
            "#!/bin/bash\n"
            "# parsidion-cc post-merge hook\n"
            "set -e\n"
            "uv run --no-project ~/.claude/skills/parsidion-cc/scripts/update_index.py\n"
            "uv run ~/.claude/skills/parsidion-cc/scripts/build_embeddings.py --incremental\n"
        )
        hook_path.write_text(legacy_body, encoding="utf-8")
        hook_path.chmod(0o755)

        claude_dir = tmp_path / ".claude"
        scripts_src = claude_dir / "skills" / "parsidion" / "scripts"
        scripts_src.mkdir(parents=True)

        install_vault_post_merge_hook(vault, claude_dir, dry_run=False)

        new_body = hook_path.read_text(encoding="utf-8")
        assert _POST_MERGE_MARKER in new_body
        assert "# parsidion-cc post-merge hook" not in new_body
        uv_run_lines = [
            ln for ln in new_body.splitlines() if ln.lstrip().startswith("uv run")
        ]
        assert all("--no-project" in ln for ln in uv_run_lines), new_body

    def test_foreign_hook_is_preserved(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        hooks_dir = vault / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_path = hooks_dir / "post-merge"

        foreign_body = "#!/bin/bash\necho 'user custom hook'\nexit 0\n"
        hook_path.write_text(foreign_body, encoding="utf-8")

        claude_dir = tmp_path / ".claude"
        (claude_dir / "skills" / "parsidion" / "scripts").mkdir(parents=True)

        install_vault_post_merge_hook(vault, claude_dir, dry_run=False)

        # Untouched: not ours.
        assert hook_path.read_text(encoding="utf-8") == foreign_body


class TestConfigureVaultGitignore:
    """SEC-104: globs cover backup variants; substring bug fixed."""

    def test_globs_cover_bak_variants(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        vault.mkdir()
        configure_vault_gitignore(vault, dry_run=False)
        content = (vault / ".gitignore").read_text(encoding="utf-8")
        # Globs must be present — they catch timestamped backups produced
        # by migration code.
        for entry in (
            "embeddings.db*",
            "pending_summaries.jsonl*",
            "dead_letters.jsonl*",
            "hook_events.log*",
            "conflicts/",
        ):
            assert entry in content
        # SEC-101 defence-in-depth entries.
        for entry in ("pyproject.toml", "uv.toml", "setup.py", ".venv/"):
            assert entry in content

    def test_commented_config_yaml_does_not_suppress_real_entry(
        self, tmp_path: Path
    ) -> None:
        # SEC-104: the old substring test treated `# config.yaml` as proof the
        # real `config.yaml` entry was present. The line-wise comparison must
        # detect the difference and append the real entry.
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / ".gitignore").write_text("# config.yaml\n", encoding="utf-8")

        configure_vault_gitignore(vault, dry_run=False)
        lines = [
            ln.strip()
            for ln in (vault / ".gitignore").read_text(encoding="utf-8").splitlines()
        ]
        assert "# config.yaml" in lines
        assert "config.yaml" in lines


class TestSec012PostMergeHookPath:
    """SEC-012: the hook must carry shell-quoted absolute script paths.

    The v1 template interpolated ``~/...`` inside double quotes, where
    neither bash nor uv expands ``~`` — with ``set -e`` every ``git pull``
    ended non-zero. The rendered hook must contain no ``"~`` sequence, must
    survive a home directory containing spaces (``bash -n``), and a stale
    v1 hook must be regenerated / removed as ours.
    """

    def _install(self, tmp_path: Path, home_name: str = "home") -> tuple[Path, Path]:
        vault = tmp_path / "vault"
        (vault / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
        claude_dir = tmp_path / home_name / ".claude"
        (claude_dir / "skills" / "parsidion" / "scripts").mkdir(parents=True)
        install_vault_post_merge_hook(vault, claude_dir, dry_run=False)
        return vault / ".git" / "hooks" / "post-merge", claude_dir

    def test_no_double_quoted_tilde(self, tmp_path: Path) -> None:
        hook_path, _ = self._install(tmp_path)
        content = hook_path.read_text(encoding="utf-8")
        assert '"~' not in content
        assert "update_index.py" in content
        assert "build_embeddings.py" in content

    def test_home_with_space_yields_valid_bash(self, tmp_path: Path) -> None:
        if shutil.which("bash") is None:
            pytest.skip("bash not available")
        hook_path, claude_dir = self._install(tmp_path, home_name="home with space")
        result = subprocess.run(
            ["bash", "-n", str(hook_path)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        content = hook_path.read_text(encoding="utf-8")
        assert '"~' not in content
        # The absolute script path under the space-containing home is present.
        assert str(claude_dir / "skills" / "parsidion" / "scripts") in content

    def test_v1_tilde_hook_is_regenerated(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        hooks_dir = vault / ".git" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook_path = hooks_dir / "post-merge"
        v1_body = (
            "#!/bin/bash\n"
            "# parsidion post-merge hook — rebuilds vault index and embeddings"
            " after pull\n"
            "set -e\n"
            'uv run --no-project "~/.claude/skills/parsidion/scripts'
            '/update_index.py"\n'
            'uv run --no-project "~/.claude/skills/parsidion/scripts'
            '/build_embeddings.py" --incremental\n'
        )
        hook_path.write_text(v1_body, encoding="utf-8")
        assert not _is_current_post_merge_hook(v1_body)  # v2 marker required

        claude_dir = tmp_path / ".claude"
        (claude_dir / "skills" / "parsidion" / "scripts").mkdir(parents=True)
        install_vault_post_merge_hook(vault, claude_dir, dry_run=False)

        new_body = hook_path.read_text(encoding="utf-8")
        assert _POST_MERGE_MARKER in new_body
        assert '"~' not in new_body

    def test_v1_tilde_hook_is_removed_on_uninstall(self, tmp_path: Path) -> None:
        vault = tmp_path / "vault"
        hook_path = vault / ".git" / "hooks" / "post-merge"
        hook_path.parent.mkdir(parents=True)
        hook_path.write_text(
            "#!/bin/bash\n# parsidion post-merge hook\nset -e\n"
            'uv run --no-project "~/x/update_index.py"\n',
            encoding="utf-8",
        )
        remove_vault_post_merge_hook(vault, dry_run=False)
        assert not hook_path.exists()
