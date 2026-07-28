"""SEC-105 regression tests for ``installer.hooks.merge_hooks``.

Three invariants pinned:

1. A malformed (e.g. trailing-comma) ``settings.json`` is left byte-identical
   and reported as a failure — never reset to ``{}`` and overwritten, which
   was the prior behaviour that silently destroyed ``permissions.deny``,
   ``permissions.allow``, ``env``, ``statusLine``, MCP servers, and every
   non-parsidion hook behind a single yellow warning.
2. A valid ``settings.json`` carrying ``permissions.deny`` and an unrelated
   ``statusLine`` key retains both after ``merge_hooks``.
3. The first mutation of a pre-existing ``settings.json`` snapshots a
   ``settings.json.bak`` next to it for manual recovery.

CWE-345.
"""

from __future__ import annotations

import json
from pathlib import Path

import install
from installer import hooks as hooks_mod


def _make_settings(tmp_path: Path, body: str) -> Path:
    """Create a settings.json with *body* (raw bytes) and return its path."""
    settings_file = tmp_path / "settings.json"
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text(body, encoding="utf-8")
    return settings_file


class TestMergeHooksBailsOnMalformed:
    """SEC-105: parse failure must never lead to a write."""

    def test_trailing_comma_leaves_file_untouched(self, tmp_path: Path, capsys) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        original = (
            "{\n"
            '  "permissions": {\n'
            '    "allow": ["Bash(git:*)"],\n'
            '    "deny": ["Bash(rm:*)"]\n'
            "  },\n"
            '  "statusLine": {"type": "command", "command": "echo hi"},\n'
            '  "hooks": {}\n'  # trailing comma on next line
            ",\n"
            "}\n"
        )
        settings_file = _make_settings(tmp_path, original)

        install.merge_hooks(claude_dir, settings_file, dry_run=False, verbose=False)

        # File must be byte-identical — not reset, not rewritten.
        assert settings_file.read_text(encoding="utf-8") == original

        err = capsys.readouterr().err
        # The user must be told something went wrong — silently dropping the
        # write was the failure mode that hid this bug for so long.
        assert "Could not parse" in err or "Could not read" in err

    def test_non_object_json_is_left_untouched(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        original = "[1, 2, 3]\n"
        settings_file = _make_settings(tmp_path, original)

        install.merge_hooks(claude_dir, settings_file, dry_run=False, verbose=False)

        assert settings_file.read_text(encoding="utf-8") == original


class TestMergeHooksPreservesUnrelatedKeys:
    """SEC-105: a successful merge must keep every pre-existing key."""

    def test_permissions_deny_statusline_and_mcp_servers_retained(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        original = {
            "permissions": {
                "allow": ["Bash(git:*)"],
                "deny": ["Bash(rm:*)", "Bash(curl:*)"],
            },
            "statusLine": {"type": "command", "command": "echo hi"},
            "mcpServers": {
                "par-mem": {"command": "/usr/local/bin/par-mem"},
            },
            "hooks": {},
        }
        settings_file = _make_settings(tmp_path, json.dumps(original, indent=2) + "\n")

        install.merge_hooks(claude_dir, settings_file, dry_run=False, verbose=False)

        merged = json.loads(settings_file.read_text(encoding="utf-8"))
        # Security-critical keys retained exactly.
        assert merged["permissions"]["deny"] == ["Bash(rm:*)", "Bash(curl:*)"]
        assert merged["permissions"]["allow"] == ["Bash(git:*)"]
        # Unrelated user configuration retained.
        assert merged["statusLine"] == {"type": "command", "command": "echo hi"}
        assert merged["mcpServers"]["par-mem"]["command"] == "/usr/local/bin/par-mem"
        # And the hooks we came to install are present.
        assert "hooks" in merged
        assert len(merged["hooks"]) > 0


class TestMergeHooksBackupOnFirstMutation:
    """SEC-105: a ``.bak`` is created before the first mutation of a
    pre-existing ``settings.json`` so a botched merge is recoverable."""

    def test_backup_created_when_merging_pre_existing_file(
        self, tmp_path: Path
    ) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        original = {
            "permissions": {"deny": ["Bash(rm:*)"]},
            "hooks": {},
        }
        original_text = json.dumps(original, indent=2) + "\n"
        settings_file = _make_settings(tmp_path, original_text)

        install.merge_hooks(claude_dir, settings_file, dry_run=False, verbose=False)

        backup = settings_file.with_suffix(settings_file.suffix + ".bak")
        assert backup.exists()
        # Backup is the pre-mutation bytes.
        assert backup.read_text(encoding="utf-8") == original_text

    def test_no_backup_for_fresh_create(self, tmp_path: Path) -> None:
        # A brand-new settings.json (installer-created) has no prior content
        # to back up; the helper must not invent a backup in that case.
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        # Confirm fixture sanity.
        assert not settings_file.exists()

        install.merge_hooks(claude_dir, settings_file, dry_run=False, verbose=False)

        backup = settings_file.with_suffix(settings_file.suffix + ".bak")
        assert not backup.exists()


class TestAtomicWriteJson:
    """SEC-105 / ARC-018: the shared atomic-write helper preserves mode and
    is crash-safe by construction (tmp + os.replace)."""

    def test_preserves_existing_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        path.write_text("{}", encoding="utf-8")
        path.chmod(0o600)
        mode_before = path.stat().st_mode & 0o777

        hooks_mod._atomic_write_json(path, {"hello": "world"})

        mode_after = path.stat().st_mode & 0o777
        assert mode_after == mode_before
        assert json.loads(path.read_text(encoding="utf-8")) == {"hello": "world"}

    def test_no_tmp_file_left_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "settings.json"
        hooks_mod._atomic_write_json(path, {"x": 1})
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []
