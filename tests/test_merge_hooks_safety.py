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

import installer.hooks as installer_hooks
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

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

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

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

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

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

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

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        backup = settings_file.with_suffix(settings_file.suffix + ".bak")
        assert backup.exists()
        # Backup is the pre-mutation bytes.
        assert backup.read_text(encoding="utf-8") == original_text

    def test_backup_inherits_source_mode(self, tmp_path: Path) -> None:
        """SEC-025: the .bak mirrors settings.json's mode, not the umask."""
        import os

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        original_text = json.dumps({"hooks": {}}, indent=2) + "\n"
        settings_file = _make_settings(tmp_path, original_text)
        os.chmod(settings_file, 0o600)

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        backup = settings_file.with_suffix(settings_file.suffix + ".bak")
        assert backup.exists()
        assert backup.stat().st_mode & 0o777 == 0o600, oct(backup.stat().st_mode)

    def test_no_backup_for_fresh_create(self, tmp_path: Path) -> None:
        # A brand-new settings.json (installer-created) has no prior content
        # to back up; the helper must not invent a backup in that case.
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        # Confirm fixture sanity.
        assert not settings_file.exists()

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

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


class TestConcurrentMergeHooksSerialized:
    """ARC-018: two concurrent merge_hooks calls must not lose either side's
    changes. The flock sidecar (``settings.json.lock``) serialises the two
    read-modify-write cycles — without it the second writer would clobber the
    first writer's hook additions on read.
    """

    def test_two_concurrent_calls_keep_both_changes(self, tmp_path: Path) -> None:
        import threading

        from installer.hooks import _HOOK_SCRIPTS

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}\n", encoding="utf-8")

        # The first call registers parsidion's hooks; the second call (which
        # would be a no-op on its own) runs concurrently and must observe the
        # first call's writes via the lock — both calls return without either
        # side's state being lost.
        errors: list[BaseException] = []

        def _run() -> None:
            try:
                installer_hooks.merge_hooks(
                    claude_dir, settings_file, dry_run=False, verbose=False
                )
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == [], f"concurrent merge_hooks raised: {errors}"

        merged = json.loads(settings_file.read_text(encoding="utf-8"))
        # Every managed event must be present (no lost write).
        events = set(_HOOK_SCRIPTS.keys())
        assert set(merged.get("hooks", {}).keys()) >= events, (
            f"lost a hook event under concurrent merge: missing={events - set(merged.get('hooks', {}).keys())}"
        )

    def test_lock_sidecar_is_created_and_reused(self, tmp_path: Path) -> None:
        # The lock sidecar is a sibling of the target. It must be created on
        # first call and left in place (a stale .lock is harmless — flock is
        # per-fd — and removing it would race with any waiter).
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}\n", encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        lock = settings_file.parent / (settings_file.name + ".lock")
        assert lock.exists(), "flock sidecar was not created"

        # A second call must reuse it, not error and not duplicate.
        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )
        locks = [p for p in settings_file.parent.iterdir() if p.name.endswith(".lock")]
        assert len(locks) == 1, f"expected exactly one lock sidecar, got {locks}"


class TestSessionStartTimeout:
    """SessionStart gets a 60s timeout (installer.paths._HOOK_OPTIONS),
    matching the codex (60s) and omp/pi (60s) registrations — headless AI
    selector backends run 8-40s. enable_ai_mode no longer changes it."""

    @staticmethod
    def _session_start_timeouts(settings_file: Path) -> list[object]:
        merged = json.loads(settings_file.read_text(encoding="utf-8"))
        return [
            h.get("timeout")
            for entry in merged.get("hooks", {}).get("SessionStart", [])
            for h in entry.get("hooks", [])
        ]

    def test_new_registration_gets_60000(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}\n", encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        timeouts = self._session_start_timeouts(settings_file)
        assert 60000 in timeouts, f"SessionStart timeout not 60000ms: {timeouts}"

    def test_existing_lower_timeout_is_raised(self, tmp_path: Path) -> None:
        """Reinstall must raise a legacy 10s registration, not skip it."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}\n", encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )
        # Downgrade to the legacy value, then merge again.
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        handler = settings["hooks"]["SessionStart"][0]["hooks"][0]
        handler["timeout"] = 10000
        settings_file.write_text(json.dumps(settings), encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        timeouts = self._session_start_timeouts(settings_file)
        assert timeouts == [60000], (
            f"legacy 10s SessionStart timeout not raised to 60000ms: {timeouts}"
        )

    def test_enable_ai_mode_does_not_change_timeout(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{}\n", encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
            enable_ai_mode=True,
        )

        timeouts = self._session_start_timeouts(settings_file)
        assert 30000 not in timeouts
        assert timeouts == [60000]
