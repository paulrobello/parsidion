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


def test_connect_choices_match_registry() -> None:
    """ENH-006: the connect/disconnect agent choices are the registered runtimes.

    Guards the class of drift where the CLI hardcodes a runtime list that
    disagrees with the registry.
    """
    import agent_adapter  # noqa: PLC0415
    import install  # noqa: PLC0415

    assert set(install._connectable_runtimes()) == set(agent_adapter.known_runtimes())


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


# ---------------------------------------------------------------------------
# SEC-116: connect codex/gemini must refuse symlinks that escape the agent
# config dir, and disconnect must remove the instructions block + revert
# the [features] hooks flag.
# ---------------------------------------------------------------------------


class TestSec116SymlinkGuard:
    """Refuse to inject into a symlinked AGENTS.md that escapes ~/.codex/."""

    def test_symlink_escaping_config_dir_is_refused(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # AGENTS.md inside .codex points at a file in a *sibling* dir.
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        outside = tmp_path / "shared"
        outside.mkdir()
        target = outside / "CLAUDE.md"
        target.write_text("# global\n", encoding="utf-8")
        agents_md = codex_home / "AGENTS.md"
        agents_md.symlink_to(target)

        skill.install_codex_agents_md(codex_home)

        # Target file content is unchanged.
        assert target.read_text(encoding="utf-8") == "# global\n"
        # The symlink itself still exists.
        assert agents_md.is_symlink()

    def test_symlink_inside_config_dir_is_followed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # AGENTS.md inside .codex points at another file inside .codex/
        # — benign case, injection proceeds.
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        target = codex_home / "real.md"
        target.write_text("# real\n", encoding="utf-8")
        agents_md = codex_home / "AGENTS.md"
        agents_md.symlink_to(target)

        skill.install_codex_agents_md(codex_home)

        # Atomic write replaces the symlink with a regular file holding
        # the injected content; the target file is left unchanged. The
        # guard's job is to permit the write — the file-system effect on
        # the symlink target is the standard atomic-write semantics.
        assert not agents_md.is_symlink()
        text = agents_md.read_text(encoding="utf-8")
        assert _BEGIN in text and _END in text
        assert target.read_text(encoding="utf-8") == "# real\n"


class TestSec116RemoveInstructionsBlock:
    """``_remove_instructions_block`` strips the parsidion block idempotently."""

    def test_removes_block_and_preserves_user_content(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir()
        skill.install_gemini_md(gemini_home)
        # User appends their own content after install.
        gemini_md = gemini_home / "GEMINI.md"
        gemini_md.write_text(
            gemini_md.read_text(encoding="utf-8") + "\n# my rules\n",
            encoding="utf-8",
        )

        removed = skill.remove_gemini_md(gemini_home)
        assert removed is True
        text = gemini_md.read_text(encoding="utf-8")
        assert _BEGIN not in text
        assert _END not in text
        assert "parsidion-test-sentinel-9f2a" not in text
        assert "# my rules" in text

    def test_idempotent_second_call_returns_false(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir()
        skill.install_gemini_md(gemini_home)
        assert skill.remove_gemini_md(gemini_home) is True
        # Second call: nothing to remove.
        assert skill.remove_gemini_md(gemini_home) is False

    def test_missing_file_returns_false(self, tmp_path: Path) -> None:
        gemini_home = tmp_path / ".gemini"
        gemini_home.mkdir()
        assert skill.remove_gemini_md(gemini_home) is False

    def test_removing_block_via_symlink_that_escapes_is_refused(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # connect would have refused to write this in the first place, but
        # defense in depth: disconnect must also refuse so a planted
        # symlink cannot trick it into editing a shared file.
        monkeypatch.setattr(
            skill, "AGENT_INSTRUCTIONS_SRC", _fake_instructions(tmp_path)
        )
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        outside = tmp_path / "shared"
        outside.mkdir()
        target = outside / "CLAUDE.md"
        target.write_text(f"# global\n{_BEGIN}\nhi\n{_END}\n", encoding="utf-8")
        agents_md = codex_home / "AGENTS.md"
        agents_md.symlink_to(target)

        assert skill.remove_codex_agents_md(codex_home) is False
        # Target content is unchanged.
        assert _BEGIN in target.read_text(encoding="utf-8")


class TestSec116RevertCodexHooksFlag:
    """``disable_codex_hooks_config`` reverts ``[features] hooks = true``."""

    def test_removes_hooks_true_line(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        config = codex_home / "config.toml"
        config.write_text(
            "[features]\nhooks = true\n[other]\nkey = 1\n", encoding="utf-8"
        )
        hooks.disable_codex_hooks_config(codex_home)
        text = config.read_text(encoding="utf-8")
        assert "hooks = true" not in text
        assert "[other]" in text
        assert "key = 1" in text

    def test_idempotent_when_already_absent(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        config = codex_home / "config.toml"
        config.write_text(
            "[features]\nhooks = false\n[other]\nkey = 1\n", encoding="utf-8"
        )
        # hooks = false is left for the human; the function no-ops on it.
        hooks.disable_codex_hooks_config(codex_home)
        text = config.read_text(encoding="utf-8")
        assert "hooks = false" in text

    def test_missing_config_is_noop(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        codex_home.mkdir()
        config = codex_home / "config.toml"
        # No-op contract on a missing config: must not raise, and must not
        # create a config the user never had. (disable_codex_hooks_config
        # returns early when config.toml is absent.)
        hooks.disable_codex_hooks_config(codex_home)
        assert not config.exists()
