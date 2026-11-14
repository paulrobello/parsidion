"""Unit tests for installer/hooks.py merge + remove logic (QA-015).

The settings.json merge/removal machinery is the installer's highest-risk
module. Coverage here is unit-scoped and complementary to test_install.py's
integration coverage of the codex/antigravity runtimes:

- command-string construction, including the SEC-003 quoted-tilde form
  (``shlex.quote`` of the path tail after ``~/``) that keeps a home path
  containing spaces from word-splitting the hook command;
- ``merge_hooks`` against the Claude settings.json path: idempotency,
  foreign-hook preservation, matcher/timeout upgrade on reinstall;
- ``remove_installed_hooks``: managed-only removal and remove idempotency.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import installer.hooks as installer_hooks
import pytest

from installer.paths import SKILL_NAME

_CLAUDE_EVENTS = dict(installer_hooks._HOOK_SCRIPTS)
_MANAGED_EVENT = "SessionStart"  # options-carrying event: timeout=60000
_MATCHER_EVENT = "PreToolUse"  # the one event with a non-empty matcher

_FOREIGN_CMD = "echo foreign-hook-keep-me"
_FOREIGN_ENTRY = {
    "matcher": "",
    "hooks": [{"type": "command", "command": _FOREIGN_CMD}],
}
_FOREIGN_PERMISSIONS = {"allow": ["Bash(ls:*)"]}
_FOREIGN_STATUSLINE = {"type": "command", "command": "cat ~/.claude/statusline.sh"}


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestManagedHookCommand:
    """SEC-003 quoting in the hook command builders."""

    def test_python_script_uses_quoted_tilde_form(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(
            installer_hooks.Path, "home", classmethod(lambda cls: fake_home)
        )
        claude_dir = fake_home / ".claude"

        command = installer_hooks._managed_hook_command(
            claude_dir, SKILL_NAME, "SessionStart"
        )

        rel = shlex.quote(".claude/skills/parsidion/scripts/session_start_hook.py")
        assert command == f"uv run --no-project ~/{rel}"

    def test_sh_script_runs_directly_without_uv_prefix(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(
            installer_hooks.Path, "home", classmethod(lambda cls: fake_home)
        )
        claude_dir = fake_home / ".claude"

        command = installer_hooks._managed_hook_command(
            claude_dir, SKILL_NAME, "SessionEnd"
        )

        script = _CLAUDE_EVENTS["SessionEnd"]  # session_stop_wrapper.sh
        rel = shlex.quote(f".claude/skills/parsidion/scripts/{script}")
        assert command == f"~/{rel}"
        assert "uv run" not in command

    def test_space_in_home_does_not_word_split(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "my home"
        fake_home.mkdir()
        monkeypatch.setattr(
            installer_hooks.Path, "home", classmethod(lambda cls: fake_home)
        )
        claude_dir = fake_home / ".claude"

        command = installer_hooks._managed_hook_command(
            claude_dir, SKILL_NAME, "SessionStart"
        )

        # SEC-003: a spaced home never enters the command string at all — the
        # `~` prefix stays tilde-form (the shell expands it without word
        # splitting), and the quoted tail guards any spaced component inside
        # the relative path. shlex.split must yield exactly one path token.
        tokens = shlex.split(command)
        assert len(tokens) == 4
        assert tokens[3].startswith("~/")
        assert tokens[3].endswith(
            f".claude/skills/{SKILL_NAME}/scripts/session_start_hook.py"
        )

    def test_outside_home_gets_fully_quoted_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(
            installer_hooks.Path, "home", classmethod(lambda cls: fake_home)
        )
        claude_dir = tmp_path / "elsewhere" / ".claude"  # NOT under fake home

        command = installer_hooks._managed_hook_command(
            claude_dir, SKILL_NAME, "SessionStart"
        )

        script_path = (
            claude_dir / "skills" / SKILL_NAME / "scripts" / "session_start_hook.py"
        )
        assert command == f"uv run --no-project {shlex.quote(script_path.as_posix())}"


class TestMergeHooksIdempotency:
    """merge_hooks against the Claude settings.json path."""

    def test_merge_creates_minimal_settings_when_absent(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"

        installer_hooks.merge_hooks(claude_dir, settings_file)

        settings = _read_settings(settings_file)
        for event in _CLAUDE_EVENTS:
            entries = settings["hooks"][event]
            assert len(entries) == 1

    def test_merge_is_idempotent_and_preserves_foreign_state(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "permissions": _FOREIGN_PERMISSIONS,
                    "statusLine": _FOREIGN_STATUSLINE,
                    "env": {"CLAUDE_CODE_EXTRA": "1"},
                    "hooks": {
                        # a foreign hook under a MANAGED event...
                        _MANAGED_EVENT: [_FOREIGN_ENTRY],
                        # ...and under an entirely unmanaged event
                        "Notification": [_FOREIGN_ENTRY],
                    },
                }
            ),
            encoding="utf-8",
        )

        installer_hooks.merge_hooks(claude_dir, settings_file)
        after_first = settings_file.read_text(encoding="utf-8")

        installer_hooks.merge_hooks(claude_dir, settings_file)
        after_second = settings_file.read_text(encoding="utf-8")

        settings = json.loads(after_second)
        # Foreign state untouched by both passes.
        assert settings["permissions"] == _FOREIGN_PERMISSIONS
        assert settings["statusLine"] == _FOREIGN_STATUSLINE
        assert settings["env"] == {"CLAUDE_CODE_EXTRA": "1"}
        assert _FOREIGN_ENTRY in settings["hooks"][_MANAGED_EVENT]
        assert settings["hooks"]["Notification"] == [_FOREIGN_ENTRY]
        # Exactly one managed registration per managed event after both passes.
        for event in _CLAUDE_EVENTS:
            commands = [
                h["command"]
                for entry in settings["hooks"][event]
                for h in entry.get("hooks", [])
            ]
            managed_command = installer_hooks._managed_hook_command(
                claude_dir, SKILL_NAME, event
            )
            assert commands.count(managed_command) == 1
        # Idempotent: the second pass wrote nothing new.
        assert after_first == after_second

    def test_merge_upgrades_stale_matcher_on_reinstall(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        stale_command = installer_hooks._managed_hook_command(
            claude_dir, SKILL_NAME, _MATCHER_EVENT
        )
        settings_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        _MATCHER_EVENT: [
                            {
                                "matcher": "",  # registered before matchers existed
                                "hooks": [
                                    {"type": "command", "command": stale_command}
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        installer_hooks.merge_hooks(claude_dir, settings_file)

        settings = _read_settings(settings_file)
        entries = settings["hooks"][_MATCHER_EVENT]
        assert len(entries) == 1
        assert entries[0]["matcher"] == installer_hooks._HOOK_MATCHERS[_MATCHER_EVENT]

    def test_merge_dry_run_leaves_file_untouched(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"permissions": _FOREIGN_PERMISSIONS}), encoding="utf-8"
        )

        installer_hooks.merge_hooks(claude_dir, settings_file, dry_run=True)

        assert json.loads(settings_file.read_text(encoding="utf-8")) == {
            "permissions": _FOREIGN_PERMISSIONS
        }


class TestRemoveInstalledHooks:
    """remove_installed_hooks removes managed entries only."""

    def test_remove_drops_only_managed_entries(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        installer_hooks.merge_hooks(claude_dir, settings_file)
        # Drop in foreign state AFTER the merge, including a foreign hook
        # sharing an event with a managed one.
        settings = _read_settings(settings_file)
        settings["hooks"][_MANAGED_EVENT].append(_FOREIGN_ENTRY)
        settings["permissions"] = _FOREIGN_PERMISSIONS
        settings_file.write_text(json.dumps(settings), encoding="utf-8")

        changed = installer_hooks.remove_installed_hooks(claude_dir, settings_file)

        assert changed is True
        settings = _read_settings(settings_file)
        assert settings["permissions"] == _FOREIGN_PERMISSIONS
        foreign_kept = [
            h
            for entry in settings["hooks"][_MANAGED_EVENT]
            for h in entry.get("hooks", [])
        ]
        assert foreign_kept == [{"type": "command", "command": _FOREIGN_CMD}]
        for event in _CLAUDE_EVENTS:
            commands = [
                h["command"]
                for entry in settings["hooks"].get(event, [])
                for h in entry.get("hooks", [])
            ]
            assert not any(
                c
                == installer_hooks._managed_hook_command(claude_dir, SKILL_NAME, event)
                for c in commands
            )

    def test_remove_without_managed_hooks_returns_false(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps({"hooks": {"SessionStart": [_FOREIGN_ENTRY]}}),
            encoding="utf-8",
        )

        changed = installer_hooks.remove_installed_hooks(claude_dir, settings_file)

        assert changed is False

    def test_remove_is_idempotent(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / "claude"
        settings_file = tmp_path / "settings.json"
        installer_hooks.merge_hooks(claude_dir, settings_file)

        assert installer_hooks.remove_installed_hooks(claude_dir, settings_file) is True
        assert (
            installer_hooks.remove_installed_hooks(claude_dir, settings_file) is False
        )
