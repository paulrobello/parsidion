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

    def test_parse_args_supports_gemini_and_all_runtime(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["install.py", "--runtime", "gemini"])
        args = install.parse_args()
        assert args.runtime == "gemini"

        monkeypatch.setattr(sys, "argv", ["install.py", "--runtime", "all"])
        args = install.parse_args()
        assert args.runtime == "all"

    def test_resolve_runtime_defaults_to_claude_for_yes(self) -> None:
        assert (
            install.resolve_runtime_choice(runtime=None, yes=True, interactive=False)
            == "claude"
        )

    def test_resolve_runtime_keeps_yes_and_noninteractive_default_claude(self) -> None:
        assert (
            install.resolve_runtime_choice(runtime=None, yes=True, interactive=True)
            == "claude"
        )
        assert (
            install.resolve_runtime_choice(runtime=None, yes=False, interactive=False)
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

    def test_resolve_runtime_interactive_accepts_gemini_and_all(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(install, "_ask", lambda prompt, default="": "3")
        assert (
            install.resolve_runtime_choice(runtime=None, yes=False, interactive=True)
            == "gemini"
        )

        monkeypatch.setattr(install, "_ask", lambda prompt, default="": "5")
        assert (
            install.resolve_runtime_choice(runtime=None, yes=False, interactive=True)
            == "all"
        )

    def test_runtime_predicates_include_all_without_cross_wiring_gemini(self) -> None:
        assert install._wants_claude_runtime("all") is True
        assert install._wants_codex_runtime("all") is True
        assert install._wants_gemini_runtime("all") is True
        assert install._wants_gemini_runtime("gemini") is True
        assert install._wants_claude_runtime("gemini") is False
        assert install._wants_codex_runtime("gemini") is False


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

    def test_merge_codex_hooks_preserves_malformed_event_entries(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        hooks_file = codex_home / "hooks.json"
        hooks_file.parent.mkdir(parents=True)
        hooks_file.write_text(
            json.dumps({"hooks": {"SessionStart": ["bad"]}}) + "\n",
            encoding="utf-8",
        )

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)

        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        handlers = hooks["hooks"]["SessionStart"]
        assert "bad" in handlers
        commands = [
            hook["command"]
            for entry in handlers
            if isinstance(entry, dict)
            for hook in entry["hooks"]
        ]
        assert any("codex_session_start_hook.py" in command for command in commands)

    def test_remove_codex_hooks_preserves_malformed_mixed_entries(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        hooks_file = codex_home / "hooks.json"
        managed_command = install._managed_codex_hook_command(claude_dir, "Stop")
        malformed_entry = "bad"
        non_list_hooks_entry = {"matcher": "keep", "hooks": "bad"}
        user_entry = {
            "matcher": "",
            "hooks": [{"type": "command", "command": "echo user"}],
        }
        hooks_file.parent.mkdir(parents=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            malformed_entry,
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": managed_command},
                                    {"type": "command", "command": "echo keep"},
                                ],
                            },
                            non_list_hooks_entry,
                            user_entry,
                        ]
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        changed = install.remove_codex_hooks(codex_home, claude_dir, dry_run=False)

        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        handlers = hooks["hooks"]["Stop"]
        assert changed is True
        assert malformed_entry in handlers
        assert non_list_hooks_entry in handlers
        assert user_entry in handlers
        commands = [
            hook["command"]
            for entry in handlers
            if isinstance(entry, dict) and isinstance(entry.get("hooks"), list)
            for hook in entry["hooks"]
            if isinstance(hook, dict)
        ]
        assert managed_command not in commands
        assert "echo keep" in commands

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


class TestGeminiHooks:
    def test_merge_gemini_hooks_creates_settings_json(self, tmp_path: Path) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"

        install.merge_gemini_hooks(
            gemini_home, claude_dir, dry_run=False, verbose=False
        )

        settings = json.loads(
            (gemini_home / "settings.json").read_text(encoding="utf-8")
        )
        assert "SessionStart" in settings["hooks"]
        assert "SessionEnd" in settings["hooks"]
        commands = [
            hook["command"]
            for group in settings["hooks"].values()
            for entry in group
            for hook in entry["hooks"]
        ]
        assert any("gemini_session_start_hook.py" in command for command in commands)
        assert any("gemini_session_end_hook.py" in command for command in commands)

    def test_merge_gemini_hooks_preserves_existing_settings_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"
        settings_file = gemini_home / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [
                                    {"type": "command", "command": "echo existing"}
                                ],
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        install.merge_gemini_hooks(
            gemini_home, claude_dir, dry_run=False, verbose=False
        )
        install.merge_gemini_hooks(
            gemini_home, claude_dir, dry_run=False, verbose=False
        )

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["theme"] == "dark"
        commands = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        ]
        assert commands.count("echo existing") == 1
        assert (
            sum("gemini_session_start_hook.py" in command for command in commands) == 1
        )

    def test_remove_gemini_hooks_only_removes_managed_commands(
        self, tmp_path: Path
    ) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"
        install.merge_gemini_hooks(
            gemini_home, claude_dir, dry_run=False, verbose=False
        )
        settings_file = gemini_home / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        settings["hooks"].setdefault("SessionEnd", []).append(
            {"matcher": "*", "hooks": [{"type": "command", "command": "echo user"}]}
        )
        settings_file.write_text(
            json.dumps(settings, indent=2) + "\n", encoding="utf-8"
        )

        changed = install.remove_gemini_hooks(gemini_home, claude_dir, dry_run=False)

        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert changed is True
        commands = [
            hook["command"]
            for entries in updated["hooks"].values()
            for entry in entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        ]
        assert "echo user" in commands
        assert not any("gemini_session_" in command for command in commands)


class TestDefaultVaultPath:
    """Tests for installer default vault path selection."""

    def test_default_vault_path_uses_parsidion_vault_for_new_installs(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()

        assert install._default_vault_path(home) == home / "ParsidionVault"

    def test_default_vault_path_uses_legacy_claude_vault_when_present(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        legacy = home / "ClaudeVault"
        legacy.mkdir(parents=True)

        assert install._default_vault_path(home) == legacy

    def test_default_vault_path_prefers_parsidion_vault_when_both_exist(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        current = home / "ParsidionVault"
        legacy = home / "ClaudeVault"
        current.mkdir(parents=True)
        legacy.mkdir()

        assert install._default_vault_path(home) == current


class TestRuntimeFlow:
    """Tests for installer runtime selection flow."""

    def test_runtime_none_dry_run_install_skips_hook_registration(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
        calls: list[str] = []

        def record(name: str):
            def _inner(*args, **kwargs) -> None:
                calls.append(name)

            return _inner

        for name in (
            "install_skill",
            "install_agents",
            "install_scripts",
            "create_vault_dirs",
            "create_templates_symlink",
            "cleanup_legacy_assets",
            "merge_hooks",
            "enable_codex_hooks_config",
            "merge_codex_hooks",
            "install_claude_vault_md",
            "rebuild_index",
            "configure_vault_gitignore",
            "init_vault_git",
            "install_vault_post_merge_hook",
            "configure_vault_username",
            "configure_embeddings",
            "install_cli_tools",
            "schedule_summarizer",
            "create_vaults_config",
        ):
            monkeypatch.setattr(install, name, record(name))

        vault = tmp_path / "ClaudeVault"
        claude_dir = tmp_path / ".claude"
        codex_home = tmp_path / ".codex"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "install.py",
                "--yes",
                "--runtime",
                "none",
                "--dry-run",
                "--vault",
                str(vault),
                "--claude-dir",
                str(claude_dir),
                "--codex-home",
                str(codex_home),
            ],
        )
        args = install.parse_args()

        result = install.install(args)

        output = capsys.readouterr().out
        assert result == 0
        assert "Runtime     : none" in output
        assert "Runtime hooks: skipped (runtime none)" in output
        assert "Claude dir" not in output
        assert "Codex home" not in output
        assert "Claude hooks:" not in output
        assert "Codex hooks :" not in output
        assert "merge_hooks" not in calls
        assert "merge_codex_hooks" not in calls
        assert "enable_codex_hooks_config" not in calls

    def test_merge_codex_hooks_dry_run_does_not_create_hooks_json(
        self, tmp_path: Path
    ) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=True, verbose=False)

        assert not (codex_home / "hooks.json").exists()

    def test_runtime_both_dry_run_install_prints_codex_plan(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
        vault = tmp_path / "ClaudeVault"
        claude_dir = tmp_path / ".claude"
        codex_home = tmp_path / ".codex"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "install.py",
                "--yes",
                "--runtime",
                "both",
                "--dry-run",
                "--vault",
                str(vault),
                "--claude-dir",
                str(claude_dir),
                "--codex-home",
                str(codex_home),
            ],
        )
        args = install.parse_args()

        result = install.install(args)

        output = capsys.readouterr().out
        assert result == 0
        assert "Runtime     : both" in output
        assert f"Codex home  : {codex_home}" in output
        assert "Codex hooks : SessionStart, Stop" in output
        assert not (codex_home / "hooks.json").exists()

    def test_runtime_all_dry_run_install_prints_all_runtime_plans(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
        vault = tmp_path / "ClaudeVault"
        claude_dir = tmp_path / ".claude"
        codex_home = tmp_path / ".codex"
        gemini_home = tmp_path / ".gemini"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "install.py",
                "--yes",
                "--runtime",
                "all",
                "--dry-run",
                "--vault",
                str(vault),
                "--claude-dir",
                str(claude_dir),
                "--codex-home",
                str(codex_home),
                "--gemini-home",
                str(gemini_home),
            ],
        )
        args = install.parse_args()

        result = install.install(args)

        output = capsys.readouterr().out
        assert result == 0
        assert "Runtime     : all" in output
        assert f"Claude dir   : {claude_dir}" in output
        assert f"Codex home  : {codex_home}" in output
        assert f"Gemini home : {gemini_home}" in output
        assert (
            "Claude hooks: SessionStart, SessionEnd, PreCompact, PostCompact, SubagentStop"
            in output
        )
        assert "Codex hooks : SessionStart, Stop" in output
        assert "Gemini hooks: SessionStart, SessionEnd" in output
        assert not (codex_home / "hooks.json").exists()
        assert not (gemini_home / "settings.json").exists()

    def test_runtime_gemini_dry_run_install_prints_gemini_plan(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
        vault = tmp_path / "ClaudeVault"
        claude_dir = tmp_path / ".claude"
        gemini_home = tmp_path / ".gemini"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "install.py",
                "--yes",
                "--runtime",
                "gemini",
                "--dry-run",
                "--vault",
                str(vault),
                "--claude-dir",
                str(claude_dir),
                "--gemini-home",
                str(gemini_home),
            ],
        )
        args = install.parse_args()

        result = install.install(args)

        output = capsys.readouterr().out
        assert result == 0
        assert "Runtime     : gemini" in output
        assert f"Gemini home : {gemini_home}" in output
        assert "Gemini hooks: SessionStart, SessionEnd" in output
        assert "Claude hooks:" not in output
        assert "Codex hooks :" not in output
        assert not (gemini_home / "settings.json").exists()

    def test_uninstall_codex_runtime_removes_codex_hooks_only(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        codex_home = tmp_path / ".codex"
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        settings_file.parent.mkdir(parents=True)
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
                                        "command": install._hook_command(
                                            claude_dir, "SessionStart"
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        install.uninstall(
            claude_dir,
            settings_file,
            dry_run=False,
            yes=True,
            hooks_only=True,
            runtime="codex",
            codex_home=codex_home,
        )

        codex_hooks = json.loads(
            (codex_home / "hooks.json").read_text(encoding="utf-8")
        )
        claude_settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert codex_hooks["hooks"] == {}
        assert "SessionStart" in claude_settings["hooks"]

    def test_uninstall_gemini_runtime_removes_gemini_hooks_only(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        codex_home = tmp_path / ".codex"
        gemini_home = tmp_path / ".gemini"
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        install.merge_gemini_hooks(
            gemini_home, claude_dir, dry_run=False, verbose=False
        )
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
        codex_before = (codex_home / "hooks.json").read_text(encoding="utf-8")

        install.uninstall(
            claude_dir,
            settings_file,
            dry_run=False,
            yes=True,
            hooks_only=True,
            runtime="gemini",
            codex_home=codex_home,
            gemini_home=gemini_home,
        )

        gemini_settings = json.loads(
            (gemini_home / "settings.json").read_text(encoding="utf-8")
        )
        assert gemini_settings["hooks"] == {}
        assert (codex_home / "hooks.json").read_text(encoding="utf-8") == codex_before

    def test_uninstall_claude_runtime_leaves_codex_hooks(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        codex_home = tmp_path / ".codex"
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        before = (codex_home / "hooks.json").read_text(encoding="utf-8")

        install.uninstall(
            claude_dir,
            settings_file,
            dry_run=False,
            yes=True,
            hooks_only=True,
            runtime="claude",
            codex_home=codex_home,
        )

        assert (codex_home / "hooks.json").read_text(encoding="utf-8") == before


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
