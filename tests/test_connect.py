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

# The delimited section markers every instructions file must use.
_BEGIN = "<!-- BEGIN parsidion -->"
_END = "<!-- END parsidion -->"


class TestInstructionsInjection:
    def test_codex_agents_md_injects_section(self, tmp_path, monkeypatch):
        # Bind on `skill`, not `paths`: skill.py imports the constant at module
        # top-level, so _inject_instructions_block resolves the bare name from
        # skill's own namespace. Patching paths.AGENT_INSTRUCTIONS_SRC is inert.
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        agents_md = codex_home / "AGENTS.md"
        agents_md.write_text("# my rules\n", encoding="utf-8")
        skill.install_codex_agents_md(codex_home)
        text = agents_md.read_text(encoding="utf-8")
        assert "# my rules" in text
        assert _BEGIN in text and _END in text
        # Sentinel is unique to the FAKE source (absent from the real
        # AGENT_INSTRUCTIONS.md), so this proves the injected text came from
        # the monkeypatched path, not the real file.
        assert "parsidion-test-sentinel-9f2a" in text

    def test_gemini_md_injection_is_idempotent(self, tmp_path, monkeypatch):
        # See test_codex_agents_md_injects_section: must patch skill, not paths.
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
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
    # Include a sentinel string that does NOT appear in the real
    # skills/parsidion/AGENT_INSTRUCTIONS.md so tests can prove the injected
    # text came from this fake source, not the real one.
    p.write_text(
        "Use vault-search to recall prior notes.\nparsidion-test-sentinel-9f2a\n",
        encoding="utf-8",
    )
    return p


import install as install_mod  # noqa: E402


class TestConnectVerbs:
    def test_connect_codex_calls_install_with_codex_runtime(self, monkeypatch):
        called: dict = {}

        def fake_install(args):
            called["runtime"] = args.runtime

        monkeypatch.setattr(install_mod, "install", fake_install)
        monkeypatch.setattr(sys, "argv", ["install.py", "connect", "codex"])
        install_mod.main()
        assert called["runtime"] == "codex"

    def test_connect_gemini_calls_install_with_gemini_runtime(self, monkeypatch):
        called: dict = {}

        def fake_install(args):
            called["runtime"] = args.runtime

        monkeypatch.setattr(install_mod, "install", fake_install)
        monkeypatch.setattr(sys, "argv", ["install.py", "connect", "gemini"])
        install_mod.main()
        assert called["runtime"] == "gemini"

    def test_disconnect_codex_calls_uninstall_with_codex_runtime(self, monkeypatch):
        called: dict = {}

        def fake_uninstall(*f_args, **f_kwargs):
            # The real uninstall() is called as
            # uninstall(claude_dir, settings_file, runtime=..., ...) — runtime
            # arrives as a keyword arg, not on a namespace.
            called["runtime"] = f_kwargs.get("runtime")

        monkeypatch.setattr(install_mod, "uninstall", fake_uninstall)
        monkeypatch.setattr(sys, "argv", ["install.py", "disconnect", "codex"])
        install_mod.main()
        assert called["runtime"] == "codex"
