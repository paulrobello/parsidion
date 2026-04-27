"""Tests for install.py CLI and uninstall flows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import install


LEGACY_PROJECT_NAME = "parsidion" + "-cc"
LEGACY_SKILL_SCRIPT = (
    f"~/.claude/skills/{LEGACY_PROJECT_NAME}/scripts/session_start_hook.py"
)


class TestParseArgs:
    """Tests for installer CLI argument parsing."""

    def test_parse_args_supports_uninstall_hooks(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["install.py", "--uninstall-hooks"])

        args = install.parse_args()

        assert args.uninstall_hooks is True
        assert args.uninstall is False

    def test_parse_args_supports_runtime_and_codex_home(self, monkeypatch) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            ["install.py", "--runtime", "both", "--codex-home", "~/CustomCodex"],
        )

        args = install.parse_args()

        assert args.runtime == "both"
        assert args.codex_home == "~/CustomCodex"

    def test_resolve_runtime_defaults_to_claude_for_yes(self) -> None:
        assert (
            install.resolve_runtime_choice(runtime=None, yes=True, interactive=False)
            == "claude"
        )

    def test_resolve_runtime_defaults_to_both_for_interactive(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(install, "_ask", lambda prompt, default="": "")

        assert (
            install.resolve_runtime_choice(runtime=None, yes=False, interactive=True)
            == "both"
        )


class TestCodexHooks:
    def test_merge_codex_hooks_creates_hooks_json(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)

        hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
        assert "SessionStart" in hooks["hooks"]
        assert "Stop" in hooks["hooks"]
        commands = [
            hook["command"]
            for group in hooks["hooks"].values()
            for entry in group
            for hook in entry["hooks"]
        ]
        assert any("codex_session_start_hook.py" in command for command in commands)
        assert any("codex_stop_hook.py" in command for command in commands)

    def test_merge_codex_hooks_preserves_existing_hooks_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        hooks_file = codex_home / "hooks.json"
        hooks_file.parent.mkdir(parents=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": "echo existing"}
                                ],
                            }
                        ]
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)

        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        handlers = hooks["hooks"]["SessionStart"]
        commands = [hook["command"] for entry in handlers for hook in entry["hooks"]]
        assert commands.count("echo existing") == 1
        assert (
            sum("codex_session_start_hook.py" in command for command in commands) == 1
        )

    def test_remove_codex_hooks_only_removes_managed_commands(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        hooks_file = codex_home / "hooks.json"
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks["hooks"].setdefault("Stop", []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}
        )
        hooks_file.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")

        changed = install.remove_codex_hooks(codex_home, claude_dir, dry_run=False)

        updated = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert changed is True
        assert updated["hooks"]["Stop"] == [
            {"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}
        ]

    def test_enable_codex_hooks_config_creates_features_section(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"

        install.enable_codex_hooks_config(codex_home, dry_run=False, yes=True)

        assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
            "[features]\ncodex_hooks = true\n"
        )


class TestUninstallHooksOnly:
    """Tests for removing only managed hook registrations."""

    def test_uninstall_hooks_only_removes_managed_hooks_and_leaves_other_assets(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        skill_dir = claude_dir / "skills" / "parsidion"
        claude_vault_md = claude_dir / "CLAUDE-VAULT.md"
        agent_file = claude_dir / "agents" / install.AGENT_SRCS[0].name

        skill_dir.mkdir(parents=True)
        claude_vault_md.parent.mkdir(parents=True, exist_ok=True)
        claude_vault_md.write_text("vault guidance\n", encoding="utf-8")
        agent_file.parent.mkdir(parents=True, exist_ok=True)
        agent_file.write_text("agent\n", encoding="utf-8")

        managed_hooks = {}
        for event in install._HOOK_SCRIPTS:
            managed_hooks[event] = [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": install._hook_command(claude_dir, event),
                            "timeout": 10000,
                        }
                    ],
                }
            ]

        settings = {
            "theme": "dark",
            "hooks": {
                **managed_hooks,
                "SessionStart": managed_hooks["SessionStart"]
                + [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo keep-me",
                                "timeout": 1000,
                            }
                        ],
                    }
                ],
                "UserPromptSubmit": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo unrelated",
                                "timeout": 1000,
                            }
                        ],
                    }
                ],
            },
        }
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

        install.uninstall(
            claude_dir, settings_file, dry_run=False, yes=True, hooks_only=True
        )

        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert updated["theme"] == "dark"
        assert updated["hooks"]["SessionStart"] == [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo keep-me",
                        "timeout": 1000,
                    }
                ],
            }
        ]
        assert "SessionEnd" not in updated["hooks"]
        assert "PreCompact" not in updated["hooks"]
        assert "PostCompact" not in updated["hooks"]
        assert "SubagentStop" not in updated["hooks"]
        assert updated["hooks"]["UserPromptSubmit"] == [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo unrelated",
                        "timeout": 1000,
                    }
                ],
            }
        ]

        assert skill_dir.exists()
        assert claude_vault_md.exists()
        assert agent_file.exists()


class TestFullUninstall:
    """Tests for removing installed assets during full uninstall."""

    def test_uninstall_removes_symlinked_current_skill_dir(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setattr(
            install, "unschedule_summarizer", lambda dry_run=False: None
        )

        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        real_skill_dir = tmp_path / "real-parsidion-skill"
        skill_link = claude_dir / "skills" / "parsidion"

        real_skill_dir.mkdir()
        skill_link.parent.mkdir(parents=True)
        skill_link.symlink_to(real_skill_dir, target_is_directory=True)

        install.uninstall(
            claude_dir, settings_file, dry_run=False, yes=True, hooks_only=False
        )

        assert not skill_link.exists()
        assert not skill_link.is_symlink()
        assert real_skill_dir.exists()


class TestParsidionRenamePaths:
    """Tests for the hard rename from parsidion-cc to parsidion."""

    def test_hook_command_uses_parsidion_skill_path(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"

        command = install._hook_command(claude_dir, "SessionStart")

        assert "skills/parsidion/scripts/session_start_hook.py" in command
        assert "parsidion-cc" not in command

    def test_install_skill_uses_parsidion_destination(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        vault_root = tmp_path / "ClaudeVault"

        dest = install.install_skill(
            claude_dir,
            vault_root,
            dry_run=True,
            force=True,
            verbose=False,
        )

        assert dest == claude_dir / "skills" / "parsidion"

    def test_install_skill_creates_missing_skills_parent(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        vault_root = tmp_path / "ClaudeVault"
        claude_dir.mkdir()

        dest = install.install_skill(
            claude_dir,
            vault_root,
            dry_run=False,
            force=True,
            verbose=False,
        )

        assert dest == claude_dir / "skills" / "parsidion"
        assert dest.exists()
        assert (claude_dir / "skills").exists()


class TestLegacyCleanup:
    """Tests for automatic cleanup of managed parsidion-cc assets."""

    def test_cleanup_legacy_hooks_removes_old_commands_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_command = f"uv run --no-project {LEGACY_SKILL_SCRIPT}"
        unrelated_wrapper_command = f"echo {LEGACY_SKILL_SCRIPT}"
        new_command = install._hook_command(claude_dir, "SessionStart")
        settings = {
            "theme": "dark",
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": legacy_command,
                                "timeout": 10000,
                            }
                        ],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": unrelated_wrapper_command,
                                "timeout": 1000,
                            }
                        ],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo keep-me",
                                "timeout": 1000,
                            }
                        ],
                    },
                ],
                "SessionEnd": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": new_command,
                                "timeout": 10000,
                            }
                        ],
                    }
                ],
            },
        }
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
        )

        assert changed is True
        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert updated["theme"] == "dark"
        session_start = updated["hooks"]["SessionStart"]
        assert session_start == [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": unrelated_wrapper_command,
                        "timeout": 1000,
                    }
                ],
            },
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo keep-me",
                        "timeout": 1000,
                    }
                ],
            },
        ]
        assert updated["hooks"]["SessionEnd"] == settings["hooks"]["SessionEnd"]

    def test_cleanup_legacy_hooks_removes_custom_claude_dir_legacy_command(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".custom-claude"
        settings_file = claude_dir / "settings.json"
        legacy_command = (
            "uv run --no-project "
            f"{claude_dir.as_posix()}/skills/{LEGACY_PROJECT_NAME}/scripts/session_start_hook.py"
        )
        unrelated_wrapper_command = f"echo {legacy_command}"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": legacy_command,
                                "timeout": 10000,
                            }
                        ],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": unrelated_wrapper_command,
                                "timeout": 1000,
                            }
                        ],
                    },
                ]
            }
        }
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
        )

        assert changed is True
        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert updated["hooks"]["SessionStart"] == [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": unrelated_wrapper_command,
                        "timeout": 1000,
                    }
                ],
            }
        ]

    def test_cleanup_legacy_assets_removes_old_skill_dir(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_skill = claude_dir / "skills" / LEGACY_PROJECT_NAME
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SENTINEL.txt").write_text("legacy\n", encoding="utf-8")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text('{"hooks": {}}\n', encoding="utf-8")

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
        )

        assert changed is True
        assert not legacy_skill.exists()

    def test_cleanup_legacy_assets_dry_run_does_not_delete(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_skill = claude_dir / "skills" / LEGACY_PROJECT_NAME
        legacy_skill.mkdir(parents=True)
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": f"uv run --no-project {LEGACY_SKILL_SCRIPT}",
                                        "timeout": 10000,
                                    }
                                ],
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=True,
            verbose=False,
        )

        assert changed is True
        assert legacy_skill.exists()
        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert (
            "parsidion-cc" in updated["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        )
