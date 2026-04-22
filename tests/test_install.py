"""Tests for install.py CLI and uninstall flows."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import install


class TestParseArgs:
    """Tests for installer CLI argument parsing."""

    def test_parse_args_supports_uninstall_hooks(self, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["install.py", "--uninstall-hooks"])

        args = install.parse_args()

        assert args.uninstall_hooks is True
        assert args.uninstall is False


class TestUninstallHooksOnly:
    """Tests for removing only managed hook registrations."""

    def test_uninstall_hooks_only_removes_managed_hooks_and_leaves_other_assets(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        skill_dir = claude_dir / "skills" / "parsidion-cc"
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
        settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

        install.uninstall(claude_dir, settings_file, dry_run=False, yes=True, hooks_only=True)

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
