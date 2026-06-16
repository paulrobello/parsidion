"""Tests for the connect verb, codex feature-flag fix, and instructions injection."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import installer.hooks as hooks  # noqa: E402


def _apply(config: Path, *, yes: bool) -> str:
    """Drive _set_codex_hooks_in_features_section the way enable_codex_hooks_config does."""
    updated = hooks._set_codex_hooks_in_features_section(
        config.read_text(encoding="utf-8"), yes=yes
    )
    assert updated is not None, "installer declined to edit a safe config"
    config.write_text(updated, encoding="utf-8")
    return config.read_text(encoding="utf-8")


class TestCodexFeatureFlagName:
    def test_features_section_uses_hooks_key_not_codex_hooks(
        self, tmp_path: Path
    ) -> None:
        # Empty [features] section -> key is inserted. Exercises the insert branch.
        config = tmp_path / "config.toml"
        config.write_text("[features]\n", encoding="utf-8")
        text = _apply(config, yes=True)
        assert "hooks = true" in text
        assert "codex_hooks" not in text

    def test_existing_false_flag_flipped_to_hooks_true(self, tmp_path: Path) -> None:
        # Pre-existing disabled flag is flipped to true. Exercises the regex branch.
        config = tmp_path / "config.toml"
        config.write_text("[features]\nhooks = false\n", encoding="utf-8")
        text = _apply(config, yes=True)
        assert "hooks = true" in text
        assert "codex_hooks" not in text


import installer.skill as skill  # noqa: E402
import installer.paths as paths  # noqa: E402

# The delimited section markers every instructions file must use.
_BEGIN = "<!-- BEGIN parsidion -->"
_END = "<!-- END parsidion -->"


class TestInstructionsInjection:
    def test_codex_agents_md_injects_section(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            paths, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        agents_md = codex_home / "AGENTS.md"
        agents_md.write_text("# my rules\n", encoding="utf-8")
        skill.install_codex_agents_md(codex_home)
        text = agents_md.read_text(encoding="utf-8")
        assert "# my rules" in text
        assert _BEGIN in text and _END in text
        assert "vault-search" in text  # the shared content is present

    def test_gemini_md_injection_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            paths, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir()
        skill.install_gemini_md(gemini_home)
        before = (gemini_home / "GEMINI.md").read_text(encoding="utf-8")
        skill.install_gemini_md(gemini_home)  # second call must not duplicate
        after = (gemini_home / "GEMINI.md").read_text(encoding="utf-8")
        assert before.count(_BEGIN) == 1
        assert after == before


def _fake_instructions(tmp_path: Path) -> Path:
    p = tmp_path / "AGENT_INSTRUCTIONS.md"
    p.write_text("Use vault-search to recall prior notes.\n", encoding="utf-8")
    return p
