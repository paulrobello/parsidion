"""Hook registration and removal for all supported runtimes.

Handles merging and removing Parsidion-managed hooks in:
  - Claude Code settings.json
  - Codex hooks.json
  - Gemini settings.json

Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from installer.paths import (
    _CODEX_HOOK_SCRIPTS,
    _GEMINI_HOOK_NAMES,
    _GEMINI_HOOK_SCRIPTS,
    _HOOK_OPTIONS,
    _HOOK_SCRIPTS,
    SKILL_NAME,
)
from installer.ui import _err, _ok, _print, _step, _warn

# ---------------------------------------------------------------------------
# Hook command builders
# ---------------------------------------------------------------------------


def _managed_hook_command(claude_dir: Path, skill_name: str, event: str) -> str:
    """Return the managed hook command string for a skill and event."""
    script = _HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / skill_name / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        rel_str = f"~/{rel.as_posix()}"
    except ValueError:
        rel_str = script_path.as_posix()

    if script.endswith(".sh"):
        return rel_str
    return f"uv run --no-project {rel_str}"


def _hook_command(claude_dir: Path, event: str) -> str:
    """Return the hook command string for a given event.

    Uses ~ notation so the path is portable across user accounts.
    Shell scripts (.sh) are invoked directly; Python scripts are run via
    ``uv run --no-project`` to ensure the correct Python interpreter.
    """
    return _managed_hook_command(claude_dir, SKILL_NAME, event)


def _managed_codex_hook_command(claude_dir: Path, event: str) -> str:
    """Return the managed Codex hook command string for a Codex event."""
    script = _CODEX_HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        script_display = f"~/{rel.as_posix()}"
    except ValueError:
        script_display = script_path.as_posix()
    return f"uv run --no-project {script_display}"


def _managed_gemini_hook_command(claude_dir: Path, event: str) -> str:
    """Return the managed Gemini hook command string for a Gemini event."""
    script = _GEMINI_HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        script_display = f"~/{rel.as_posix()}"
    except ValueError:
        script_display = script_path.as_posix()
    return f"uv run --no-project {script_display}"


def _legacy_hook_command(claude_dir: Path, event: str) -> str:
    """Return the legacy managed hook command string for a given event."""
    from installer.paths import LEGACY_SKILL_NAME

    return _managed_hook_command(claude_dir, LEGACY_SKILL_NAME, event)


# ---------------------------------------------------------------------------
# Hook entry helpers
# ---------------------------------------------------------------------------


def _normalize_hook_command(command: str) -> str:
    """Return *command* normalized for exact hook command comparisons."""
    return command.replace("\\", "/").strip()


def _is_legacy_managed_hook_command(command: str, claude_dir: Path, event: str) -> bool:
    """Return True when *command* is an exact managed parsidion-cc legacy hook."""
    return _normalize_hook_command(command) == _normalize_hook_command(
        _legacy_hook_command(claude_dir, event)
    )


def _hook_already_registered(hooks_list: list[dict], command: str) -> bool:
    """Return True if any entry in hooks_list already has this command."""
    return _find_hook_handler(hooks_list, command) is not None


def _find_hook_handler(hooks_list: list[dict], command: str) -> dict | None:
    """Return the hook handler dict matching *command*, or None."""
    for entry in hooks_list:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command", "") == command:
                return hook
    return None


def _filter_hook_entries(
    event_hooks: list[dict],
    predicate,
) -> tuple[list[dict], bool]:
    """Remove hook handlers matching *predicate* while preserving unrelated hooks.

    Empty hook entries are removed. Returns the filtered entries and whether
    anything changed.
    """
    filtered_entries: list[dict] = []
    changed = False

    for entry in event_hooks:
        if not isinstance(entry, dict):
            filtered_entries.append(entry)
            continue
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            filtered_entries.append(entry)
            continue

        kept_hooks = []
        for hook in hooks:
            if isinstance(hook, dict) and predicate(hook):
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            filtered_entries.append(new_entry)
        else:
            changed = True

    return filtered_entries, changed


# ---------------------------------------------------------------------------
# Codex hooks file helpers
# ---------------------------------------------------------------------------


def _codex_hooks_file(codex_home: Path) -> Path:
    """Return the Codex hooks.json path."""
    return codex_home / "hooks.json"


def _read_codex_hooks(hooks_file: Path) -> dict | None:
    """Read Codex hooks JSON, returning None when existing data is unsafe to edit."""
    if not hooks_file.exists():
        return {"hooks": {}}
    try:
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read {hooks_file}: {exc}; skipping Codex hook update")
        return None
    if not isinstance(hooks, dict):
        _warn(f"{hooks_file} is not a JSON object; skipping Codex hook update")
        return None
    hooks_section = hooks.setdefault("hooks", {})
    if not isinstance(hooks_section, dict):
        _warn(f"{hooks_file} has non-object hooks section; skipping Codex hook update")
        return None
    return hooks


# ---------------------------------------------------------------------------
# Gemini settings file helpers
# ---------------------------------------------------------------------------


def _gemini_settings_file(gemini_home: Path) -> Path:
    """Return the Gemini settings.json path."""
    return gemini_home / "settings.json"


def _read_gemini_settings(settings_file: Path) -> dict | None:
    """Read Gemini settings JSON, returning None when existing data is unsafe to edit."""
    if not settings_file.exists():
        return {"hooks": {}}
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read {settings_file}: {exc}; skipping Gemini hook update")
        return None
    if not isinstance(settings, dict):
        _warn(f"{settings_file} is not a JSON object; skipping Gemini hook update")
        return None
    hooks_section = settings.setdefault("hooks", {})
    if not isinstance(hooks_section, dict):
        _warn(
            f"{settings_file} has non-object hooks section; skipping Gemini hook update"
        )
        return None
    return settings


# ---------------------------------------------------------------------------
# Codex hook management
# ---------------------------------------------------------------------------


def merge_codex_hooks(
    codex_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Merge Parsidion-managed Codex hooks into CODEX_HOME/hooks.json."""
    hooks_file = _codex_hooks_file(codex_home)
    hooks = _read_codex_hooks(hooks_file)
    if hooks is None:
        return

    hooks_section: dict = hooks["hooks"]
    added: list[str] = []
    skipped: list[str] = []

    for event in _CODEX_HOOK_SCRIPTS:
        command = _managed_codex_hook_command(claude_dir, event)
        event_hooks = hooks_section.setdefault(event, [])
        if not isinstance(event_hooks, list):
            _warn(f"Codex hook event {event} is not a list; skipping")
            continue
        if _hook_already_registered(event_hooks, command):
            _print(
                f"  Codex hook {event} already registered",
                verbose_only=True,
                verbose=verbose,
            )
            skipped.append(event)
            continue

        from installer.colors import bold, dim

        new_entry = {
            "matcher": "",
            "hooks": [{"type": "command", "command": command, "timeout": 10000}],
        }
        _step(f"Register Codex hook {bold(event)}: {dim(command)}", dry_run=dry_run)
        if not dry_run:
            event_hooks.append(new_entry)
        added.append(event)

    if dry_run:
        return

    if added:
        try:
            hooks_file.parent.mkdir(parents=True, exist_ok=True)
            hooks_file.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
            _ok(f"Updated {hooks_file}")
        except OSError as exc:
            _err(f"Could not write {hooks_file}: {exc}")
    elif skipped:
        _ok("All Codex hooks already registered")


def remove_codex_hooks(
    codex_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed Codex hook commands from hooks.json."""
    hooks_file = _codex_hooks_file(codex_home)
    hooks = _read_codex_hooks(hooks_file)
    if hooks is None:
        return False
    if not hooks_file.exists():
        _warn(f"Codex hooks.json not found: {hooks_file}")
        return False

    from installer.colors import bold

    hooks_section: dict = hooks["hooks"]
    changed = False
    for event in _CODEX_HOOK_SCRIPTS:
        command = _managed_codex_hook_command(claude_dir, event)
        event_hooks = hooks_section.get(event, [])
        if not isinstance(event_hooks, list):
            continue
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, command=command: hook.get("command", "") == command,
        )
        if event_changed:
            _step(f"Remove Codex hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            hooks_file.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")
            _ok(f"Updated {hooks_file}")
        except OSError as exc:
            _err(f"Could not write {hooks_file}: {exc}")
    elif not changed:
        _warn("No Parsidion Codex hook registrations found.")

    return changed


# ---------------------------------------------------------------------------
# Gemini hook management
# ---------------------------------------------------------------------------


def merge_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Merge Parsidion-managed Gemini hooks into GEMINI_HOME/settings.json."""
    settings_file = _gemini_settings_file(gemini_home)
    settings = _read_gemini_settings(settings_file)
    if settings is None:
        return

    hooks_section: dict = settings["hooks"]
    added: list[str] = []
    skipped: list[str] = []

    for event in _GEMINI_HOOK_SCRIPTS:
        command = _managed_gemini_hook_command(claude_dir, event)
        event_hooks = hooks_section.setdefault(event, [])
        if not isinstance(event_hooks, list):
            _warn(f"Gemini hook event {event} is not a list; skipping")
            continue
        if _hook_already_registered(event_hooks, command):
            _print(
                f"  Gemini hook {event} already registered",
                verbose_only=True,
                verbose=verbose,
            )
            skipped.append(event)
            continue

        from installer.colors import bold, dim

        new_entry = {
            "matcher": "*",
            "hooks": [
                {
                    "name": _GEMINI_HOOK_NAMES[event],
                    "type": "command",
                    "command": command,
                    "timeout": 10000,
                }
            ],
        }
        _step(f"Register Gemini hook {bold(event)}: {dim(command)}", dry_run=dry_run)
        if not dry_run:
            event_hooks.append(new_entry)
        added.append(event)

    if dry_run:
        return

    if added:
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif skipped:
        _ok("All Gemini hooks already registered")


def remove_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed Gemini hook commands from settings.json."""
    settings_file = _gemini_settings_file(gemini_home)
    settings = _read_gemini_settings(settings_file)
    if settings is None:
        return False
    if not settings_file.exists():
        _warn(f"Gemini settings.json not found: {settings_file}")
        return False

    from installer.colors import bold

    hooks_section: dict = settings["hooks"]
    changed = False
    for event in _GEMINI_HOOK_SCRIPTS:
        command = _managed_gemini_hook_command(claude_dir, event)
        event_hooks = hooks_section.get(event, [])
        if not isinstance(event_hooks, list):
            continue
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, command=command: hook.get("command", "") == command,
        )
        if event_changed:
            _step(f"Remove Gemini hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif not changed:
        _warn("No Parsidion Gemini hook registrations found.")

    return changed


# ---------------------------------------------------------------------------
# Codex config feature flag
# ---------------------------------------------------------------------------


def _set_codex_hooks_in_features_section(content: str, *, yes: bool) -> str | None:
    """Return updated Codex config text, or None when no safe edit is available."""
    from installer.ui import _confirm

    lines = content.splitlines()
    if not lines:
        return "[features]\nhooks = true\n"

    features_start: int | None = None
    features_end = len(lines)
    section_re = re.compile(r"^\s*\[([^\]]+)]\s*(?:#.*)?$")
    for index, line in enumerate(lines):
        match = section_re.match(line)
        if not match:
            continue
        section_name = match.group(1).strip()
        if section_name == "features":
            features_start = index
            features_end = len(lines)
            for end_index in range(index + 1, len(lines)):
                if section_re.match(lines[end_index]):
                    features_end = end_index
                    break
            break

    if features_start is None:
        suffix = "" if content.endswith("\n") else "\n"
        return content + suffix + "\n[features]\nhooks = true\n"

    codex_hooks_re = re.compile(
        r"^(\s*hooks\s*=\s*)(true|false)(\s*(?:#.*)?)$", re.IGNORECASE
    )
    codex_hooks_key_re = re.compile(r"^\s*hooks\s*=")
    for index in range(features_start + 1, features_end):
        match = codex_hooks_re.match(lines[index])
        if not match:
            if codex_hooks_key_re.match(lines[index]):
                _warn("Ambiguous hooks setting; leaving Codex config unchanged")
                return None
            continue
        value = match.group(2).lower()
        if value == "true":
            return content
        if not yes and not _confirm("Enable hooks in Codex config?", default=True):
            _warn("Codex hooks are disabled; add `hooks = true` manually")
            return None
        lines[index] = f"{match.group(1)}true{match.group(3)}"
        return "\n".join(lines) + "\n"

    insert_at = features_end
    lines.insert(insert_at, "hooks = true")
    return "\n".join(lines) + "\n"


def enable_codex_hooks_config(
    codex_home: Path,
    dry_run: bool = False,
    yes: bool = False,
) -> None:
    """Ensure CODEX_HOME/config.toml enables native Codex hooks."""
    config_file = codex_home / "config.toml"
    if config_file.exists():
        try:
            content = config_file.read_text(encoding="utf-8")
        except OSError as exc:
            _warn(f"Could not read {config_file}: {exc}")
            return
    else:
        content = ""

    updated = _set_codex_hooks_in_features_section(content, yes=yes)
    if updated is None:
        _warn("Add this manually to Codex config:\n[features]\nhooks = true")
        return
    if updated == content:
        _ok("Codex hooks already enabled")
        return

    _step(f"Enable Codex hooks in {config_file}", dry_run=dry_run)
    if dry_run:
        return
    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(updated, encoding="utf-8")
        _ok(f"Updated {config_file}")
    except OSError as exc:
        _err(f"Could not write {config_file}: {exc}")


# ---------------------------------------------------------------------------
# Claude (settings.json) hook management
# ---------------------------------------------------------------------------


def merge_hooks(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Load settings.json, add vault hooks if missing, write back."""
    from installer.colors import bold, dim

    settings: dict = {}
    if settings_file.exists():
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"Could not read {settings_file}: {exc}")
            settings = {}
    else:
        _warn(f"{settings_file} not found — creating a minimal one")

    hooks_section: dict = settings.setdefault("hooks", {})
    added: list[str] = []
    skipped: list[str] = []

    for event, _script_name in _HOOK_SCRIPTS.items():
        command = _hook_command(claude_dir, event)
        event_hooks: list[dict] = hooks_section.setdefault(event, [])
        desired_options = _HOOK_OPTIONS.get(event, {})

        existing_handler = _find_hook_handler(event_hooks, command)
        if existing_handler is not None:
            needs_update = any(
                existing_handler.get(k) != v for k, v in desired_options.items()
            )
            if not needs_update:
                _print(
                    dim(f"  Hook {event} already registered"),
                    verbose_only=True,
                    verbose=verbose,
                )
                skipped.append(event)
                continue
            _step(
                f"Update hook {bold(event)} options: {dim(', '.join(f'{k}={v}' for k, v in desired_options.items()))}",
                dry_run=dry_run,
            )
            if not dry_run:
                existing_handler.update(desired_options)
            added.append(event)
            continue

        hook_handler: dict = {
            "type": "command",
            "command": command,
            "timeout": 10000,
        }
        hook_handler.update(desired_options)

        new_entry: dict = {
            "matcher": "",
            "hooks": [hook_handler],
        }
        _step(f"Register hook {bold(event)}: {dim(command)}", dry_run=dry_run)
        if not dry_run:
            event_hooks.append(new_entry)
        added.append(event)

    if dry_run:
        return

    if added:
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif skipped:
        _ok("All hooks already registered")


def remove_installed_hooks(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed hook registrations from settings.json.

    Returns True when at least one managed hook registration was found.
    """
    from installer.colors import bold

    if not settings_file.exists():
        _warn(f"settings.json not found: {settings_file}")
        return False

    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read settings.json: {exc}")
        return False

    hooks_section: dict = settings.get("hooks", {})
    changed = False

    for event, _script_name in _HOOK_SCRIPTS.items():
        command = _hook_command(claude_dir, event)
        event_hooks: list[dict] = hooks_section.get(event, [])
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, command=command: hook.get("command", "") == command,
        )
        if event_changed:
            _step(f"Remove hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif not changed:
        _warn("No Parsidion hook registrations found.")

    return changed


def remove_legacy_hooks(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
) -> bool:
    """Remove managed legacy parsidion-cc hook registrations from settings.json."""
    from installer.colors import bold

    if not settings_file.exists():
        return False

    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read settings.json for legacy cleanup: {exc}")
        return False

    hooks_section: dict = settings.get("hooks", {})
    changed = False

    for event, _script_name in _HOOK_SCRIPTS.items():
        event_hooks: list[dict] = hooks_section.get(event, [])
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, event=event: _is_legacy_managed_hook_command(
                str(hook.get("command", "")), claude_dir, event
            ),
        )
        if event_changed:
            _step(f"Remove legacy hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")

    return changed
