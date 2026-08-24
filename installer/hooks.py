"""Hook registration and removal for all supported runtimes.

Handles merging and removing Parsidion-managed hooks in:
  - Claude Code settings.json
  - Codex hooks.json
  - Gemini settings.json

Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

from installer.colors import bold, dim
from installer.paths import (
    _HOOK_OPTIONS,
    _HOOK_SCRIPTS,
    LEGACY_SKILL_NAME,
    SKILL_NAME,
)
from installer.ui import _confirm, _err, _ok, _print, _step, _warn

import agent_adapter  # ENH-006: runtime registry (scripts/ on sys.path via installer/__init__)

# ---------------------------------------------------------------------------
# Atomic-write + flock helpers (SEC-105 / ARC-018)
# ---------------------------------------------------------------------------
#
# Two composition primitives used by every config read-modify-write cycle:
#   * ``_atomic_write_*`` — crash-safe writes (tmp + os.replace, mode-preserving).
#   * ``_file_lock`` — POSIX flock sidecar serialising concurrent installers.
#
# Windows has no fcntl.flock; on that platform the context manager is a no-op
# (the tmp+replace write still protects against crash truncation, just not
# against a concurrent installer process — which is rare on Windows where
# symlinks aren't used and ``install_skill`` copytree is the slower path).

try:
    import fcntl as _fcntl
except ImportError:  # Windows
    _fcntl = None  # type: ignore[assignment]


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write *data* to *path* via tmp + ``os.replace`` so a crash mid-write
    cannot truncate the destination.

    Preserves the existing file's mode when *path* already exists (so a
    0600 ``settings.json`` stays 0600). The tmp file is created in the same
    directory so ``os.replace`` is atomic on POSIX and Windows. Reused by
    ARC-018 for the remaining config-write sites.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2) + "\n"
    mode = 0o644
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        # Destination does not exist yet — fall back to default umask-derived.
        pass
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            tmp.write(payload)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_text(path: Path, data: str) -> None:
    """Write text *data* to *path* atomically (tmp + os.replace).

    Mirror of ``_atomic_write_json`` for non-JSON text files (TOML, YAML,
    shell hooks, markdown). Mode-preserving on an existing destination.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o644
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            tmp.write(data)
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def _file_lock(target: Path):
    """Hold an exclusive flock on ``<target>.lock`` for the duration of a RMW.

    Creates the sidecar lock file as a sibling of *target* (so they share a
    directory and therefore a lock namespace). No-op on Windows where
    ``fcntl`` is unavailable — the atomic write still guards against
    truncation, only the cross-process serialisation is lost.

    The sidecar is intentionally not removed on release: a stale ``.lock``
    file is harmless (flock is advisory and per-fd) and removing it would
    race with any waiter.
    """
    lock_path = target.parent / (target.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        try:
            yield
        finally:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
    finally:
        os.close(fd)


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


def _legacy_hook_command(claude_dir: Path, event: str) -> str:
    """Return the legacy managed hook command string for a given event."""
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
# Runtime hook registration (shared core)
# ---------------------------------------------------------------------------
# _merge_runtime_hooks drives one read-modify-write pass over a runtime's hook
# config, adapter-driven (ENH-006). The per-runtime merge/remove wrappers below
# delegate here; the codex/gemini file-helper + entry-shape code that used to
# live here collapsed into _read_runtime_hooks / _build_entry / the adapter.


def _merge_runtime_hooks(
    adapter: agent_adapter.AgentAdapter,
    runtime_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Shared read-modify-write core for Parsidion-managed runtime hooks.

    Drives one RMW pass over the runtime's hook config file: take a flock,
    read+validate via ``_read_runtime_hooks`` (malformed JSON / wrong shape is
    reported with the runtime's label and skipped), then iterate
    ``adapter.event_scripts`` and register any event whose managed command is
    not already present. Malformed event entries (non-dict, non-list
    ``hooks``) are preserved verbatim — only our managed command is ever
    appended. Writes go through ``_atomic_write_json`` so a crash mid-write
    cannot truncate the file.

    ENH-006: driven by an ``AgentAdapter`` + the generic helpers, replacing the
    former ``_RuntimeHookSpec``.

    Args:
        adapter: Runtime descriptor (config filename / event scripts / entry shape).
        runtime_home: Config-directory root for the runtime (``~/.codex`` /
            ``~/.gemini``); the hook file is ``runtime_home / adapter.hooks_config_filename``.
        claude_dir: Claude Code config directory (``~/.claude``) — used to
            resolve the managed hook command paths.
        dry_run: When True, print registrations but skip the file write.
        verbose: When True, emit a line per already-registered event.
    """
    label = adapter.display_name or adapter.name
    config_file = _runtime_hooks_file(adapter, runtime_home)
    with _file_lock(config_file):
        data = _read_runtime_hooks(adapter, config_file)
        if data is None:
            return

        hooks_section: dict = data["hooks"]
        added: list[str] = []
        skipped: list[str] = []

        for event in adapter.event_scripts:
            command = _build_managed_command(adapter, claude_dir, event)
            event_hooks = hooks_section.setdefault(event, [])
            if not isinstance(event_hooks, list):
                _warn(f"{label} hook event {event} is not a list; skipping")
                continue
            if _hook_already_registered(event_hooks, command):
                _print(
                    f"  {label} hook {event} already registered",
                    verbose_only=True,
                    verbose=verbose,
                )
                skipped.append(event)
                continue

            new_entry = _build_entry(adapter, event, command)
            _step(
                f"Register {label} hook {bold(event)}: {dim(command)}",
                dry_run=dry_run,
            )
            if not dry_run:
                event_hooks.append(new_entry)
            added.append(event)

        if dry_run:
            return

        if added:
            try:
                _atomic_write_json(config_file, data)
                _ok(f"Updated {config_file}")
            except OSError as exc:
                _err(f"Could not write {config_file}: {exc}")
        elif skipped:
            _ok(f"All {label} hooks already registered")


# ---------------------------------------------------------------------------
# Codex hook management
# ---------------------------------------------------------------------------


def merge_codex_hooks(
    codex_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Merge Parsidion-managed Codex hooks into ``CODEX_HOME/hooks.json``.

    Thin wrapper over the shared ``_merge_runtime_hooks`` core; all
    per-runtime behaviour (file path, reader, entry shape, timeout units)
    lives on the codex AgentAdapter (ENH-006).

    Args:
        codex_home: Path to the Codex config directory (``~/.codex`` on a
            default install). ``hooks.json`` is created or appended inside.
        claude_dir: Path to the Claude Code config directory (``~/.claude``)
            — used to resolve the managed hook command paths under
            ``skills/parsidion/scripts/``.
        dry_run: When True, print the registrations that would be made but
            leave ``hooks.json`` untouched.
        verbose: When True, emit a line per already-registered hook;
            otherwise already-registered events are silent.
    """
    _merge_runtime_hooks(_adapter("codex"), codex_home, claude_dir, dry_run, verbose)


# ---------------------------------------------------------------------------
# Generic runtime-hook core (ENH-006)
# ---------------------------------------------------------------------------
# Adapter-driven equivalents of the per-runtime helpers above. The remove side
# collapses onto ``remove_runtime_hooks``; the merge side follows in phase 3.


def _adapter(name: str) -> agent_adapter.AgentAdapter:
    """Look up a built-in adapter (always registered at agent_adapter import)."""
    adapter = agent_adapter.get(name)
    if adapter is None:
        raise RuntimeError(f"built-in adapter {name!r} is not registered")
    return adapter


def _runtime_hooks_file(
    adapter: agent_adapter.AgentAdapter, runtime_home: Path
) -> Path:
    """Resolve a runtime's hook-config file from its home dir + adapter filename."""
    if adapter.hooks_config_filename is None:
        raise ValueError(f"adapter {adapter.name!r} has no hook config file")
    return runtime_home / adapter.hooks_config_filename


def _read_runtime_hooks(
    adapter: agent_adapter.AgentAdapter, hooks_file: Path
) -> dict | None:
    """Read + validate a runtime hook config; None when unsafe to edit.

    Ensures a ``hooks`` sub-dict exists (matching the codex/gemini readers).
    """
    label = adapter.display_name or adapter.name
    if not hooks_file.exists():
        return {"hooks": {}}
    try:
        data = json.loads(hooks_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read {hooks_file}: {exc}; skipping {label} hook update")
        return None
    if not isinstance(data, dict):
        _warn(f"{hooks_file} is not a JSON object; skipping {label} hook update")
        return None
    hooks_section = data.setdefault("hooks", {})
    if not isinstance(hooks_section, dict):
        _warn(
            f"{hooks_file} has non-object hooks section; skipping {label} hook update"
        )
        return None
    return data


def _build_managed_command(
    adapter: agent_adapter.AgentAdapter, claude_dir: Path, event: str
) -> str:
    """Build the managed hook command for (adapter, event).

    ``.sh`` scripts run directly; Python via ``uv run --no-project`` — identical
    to the per-runtime ``_managed_*_hook_command`` builders.
    """
    script = adapter.event_scripts[event]
    script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        display = f"~/{rel.as_posix()}"
    except ValueError:
        display = script_path.as_posix()
    if script.endswith(".sh"):
        return display
    return f"uv run --no-project {display}"


def _build_entry(adapter: agent_adapter.AgentAdapter, event: str, command: str) -> dict:
    """Build the per-event hook entry dict from adapter declarative fields.

    Matches the former ``_build_codex_entry`` / ``_build_gemini_entry`` output
    exactly (key order included): a ``matcher`` + ``hooks`` list whose hook
    carries an optional ``name`` (Gemini schema), ``type``, ``command``,
    ``timeout``.
    """
    hook: dict[str, object] = {"type": "command", "command": command}
    if adapter.entry_names and event in adapter.entry_names:
        hook = {"name": adapter.entry_names[event], **hook}
    if adapter.entry_timeout:
        hook["timeout"] = adapter.entry_timeout
    return {"matcher": adapter.entry_matcher, "hooks": [hook]}


def remove_runtime_hooks(
    adapter: agent_adapter.AgentAdapter,
    runtime_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove Parsidion-managed hook commands for *adapter* from its config.

    Generic collapse of remove_codex_hooks / remove_gemini_hooks /
    remove_installed_hooks — identical behaviour, driven by the adapter's config
    filename, event scripts, and managed-command builder.
    """
    if adapter.hooks_config_filename is None:
        return False  # extension-only runtimes (e.g. pi) have no hook config
    label = adapter.display_name or adapter.name
    hooks_file = _runtime_hooks_file(adapter, runtime_home)
    with _file_lock(hooks_file):
        data = _read_runtime_hooks(adapter, hooks_file)
        if data is None:
            return False
        if not hooks_file.exists():
            _warn(f"{label} {adapter.hooks_config_filename} not found: {hooks_file}")
            return False
        hooks_section: dict = data["hooks"]
        changed = False
        for event in adapter.event_scripts:
            command = _build_managed_command(adapter, claude_dir, event)
            event_hooks = hooks_section.get(event, [])
            if not isinstance(event_hooks, list):
                continue
            filtered, event_changed = _filter_hook_entries(
                event_hooks,
                lambda hook, command=command: hook.get("command", "") == command,
            )
            if event_changed:
                _step(f"Remove {label} hook {bold(event)}", dry_run=dry_run)
                changed = True
                if filtered:
                    hooks_section[event] = filtered
                elif event in hooks_section:
                    del hooks_section[event]
        if changed and not dry_run:
            try:
                _atomic_write_json(hooks_file, data)
                _ok(f"Updated {hooks_file}")
            except OSError as exc:
                _err(f"Could not write {hooks_file}: {exc}")
        elif not changed:
            _warn(f"No Parsidion {label} hook registrations found.")
        return changed


def remove_codex_hooks(
    codex_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed Codex hook commands from hooks.json.

    Thin wrapper over the shared ``remove_runtime_hooks`` core (ENH-006).
    """
    return remove_runtime_hooks(_adapter("codex"), codex_home, claude_dir, dry_run)


# ---------------------------------------------------------------------------
# Gemini hook management
# ---------------------------------------------------------------------------


def merge_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Merge Parsidion-managed Gemini hooks into ``GEMINI_HOME/settings.json``.

    Thin wrapper over the shared ``_merge_runtime_hooks`` core; all
    per-runtime behaviour (file path, reader, entry shape, timeout units)
    lives on the gemini AgentAdapter (ENH-006).

    Args:
        gemini_home: Path to the Gemini config directory (``~/.gemini`` on
            a default install). ``settings.json`` is created or appended
            inside.
        claude_dir: Path to the Claude Code config directory (``~/.claude``)
            — used to resolve the managed hook command paths under
            ``skills/parsidion/scripts/``.
        dry_run: When True, print the registrations that would be made but
            leave ``settings.json`` untouched.
        verbose: When True, emit a line per already-registered hook;
            otherwise already-registered events are silent.
    """
    _merge_runtime_hooks(_adapter("gemini"), gemini_home, claude_dir, dry_run, verbose)


def remove_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed Gemini hook commands from settings.json.

    Thin wrapper over the shared ``remove_runtime_hooks`` core (ENH-006).
    """
    return remove_runtime_hooks(_adapter("gemini"), gemini_home, claude_dir, dry_run)


# ---------------------------------------------------------------------------
# Codex config feature flag
# ---------------------------------------------------------------------------


def _set_codex_hooks_in_features_section(content: str, *, yes: bool) -> str | None:
    """Return updated Codex config text, or None when no safe edit is available."""
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
    with _file_lock(config_file):
        try:
            _atomic_write_text(config_file, updated)
            _ok(f"Updated {config_file}")
        except OSError as exc:
            _err(f"Could not write {config_file}: {exc}")


def _unset_codex_hooks_in_features_section(content: str) -> str:
    """Return Codex config text with the ``[features] hooks = true`` line removed.

    SEC-116 / ARC-022: closes the disconnect asymmetry where ``connect codex``
    flipped ``[features] hooks = true`` but ``disconnect codex`` left it set.
    A leftover ``hooks = true`` does not load parsidion hooks on its own
    (those are registered in hooks.json, which disconnect does remove) but
    it does enable the Codex native hooks feature unnecessarily. Removing
    the line entirely is the safest choice; if the user has other hooks
    configured, ``hooks = true`` is the Codex default and can be re-enabled
    manually. Idempotent: if the line is absent or already removed, the
    content is returned unchanged.
    """
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        # Drop a single ``hooks = true`` line we recognize. Leave ``hooks = false``
        # untouched (the user explicitly disabled it) and leave ambiguous forms
        # (``codex_hooks = true``, custom keys) for the human to handle.
        if re.match(r"^\s*hooks\s*=\s*true\s*(?:#.*)?$", line, re.IGNORECASE):
            continue
        out.append(line)

    cleaned = "".join(out)
    # Drop an empty [features] section header left behind by the removal.
    cleaned = re.sub(
        r"\n\[features\]\s*(?=\n\[|\Z)",
        "\n",
        cleaned,
    )
    return cleaned


def disable_codex_hooks_config(
    codex_home: Path,
    dry_run: bool = False,
) -> None:
    """Revert the ``[features] hooks = true`` line added by ``enable_codex_hooks_config``.

    SEC-116: paired with ``enable_codex_hooks_config`` so disconnect
    leaves Codex config the way connect found it. No-ops when the line
    is absent. Ambiguous forms (``codex_hooks = true``, custom regex
    misses) are left for the human to edit.
    """
    config_file = codex_home / "config.toml"
    if not config_file.exists():
        return
    try:
        content = config_file.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"Could not read {config_file}: {exc}")
        return

    updated = _unset_codex_hooks_in_features_section(content)
    if updated == content:
        return

    _step(f"Remove [features] hooks=true from {config_file}", dry_run=dry_run)
    if dry_run:
        return
    with _file_lock(config_file):
        try:
            _atomic_write_text(config_file, updated)
            _ok(f"Reverted Codex hooks flag in {config_file}")
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
    enable_ai_mode: bool = False,
) -> None:
    """Load settings.json, add vault hooks if missing, write back.

    SEC-105: a parse failure on a pre-existing settings.json is a hard
    bail-out that leaves the file untouched — never a reset to ``{}``. The
    previous behaviour silently discarded the user's ``permissions.allow``,
    ``permissions.deny``, ``env``, ``statusLine``, MCP servers, and every
    non-parsidion hook behind a single yellow warning on every install.

    ARC-018: the entire RMW cycle runs under a flock sidecar
    (``settings.json.lock``) so two concurrent installers — or an installer
    racing Claude Code's own settings write — cannot lose either side's
    changes. The write itself is the SEC-105 atomic tmp+replace.
    SessionStart's timeout comes from ``installer.paths._HOOK_OPTIONS``
    (60000ms — matches the codex and omp/pi runtimes) and is applied to both
    new registrations and existing lower-valued handlers in this same RMW
    cycle. *enable_ai_mode* no longer changes the timeout (the 60s budget
    covers every selector backend); it only drives the vault-config half in
    ``installer.skill``.
    """
    claude = _adapter("claude")
    with _file_lock(settings_file):
        pre_existing = settings_file.exists()
        original_bytes: bytes | None = None
        settings: dict = {}
        if pre_existing:
            try:
                original_bytes = settings_file.read_bytes()
                settings = json.loads(original_bytes.decode("utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                # Bail out: do NOT reset to {} and do NOT write. Mirrors the
                # Codex/Gemini readers at _read_codex_hooks/_read_gemini_settings
                # and remove_installed_hooks. SEC-105.
                _err(
                    f"Could not parse {settings_file}: {exc}\n"
                    "       Leaving it untouched. Fix the syntax (often a stray "
                    "trailing comma) and re-run."
                )
                return
            if not isinstance(settings, dict):
                _err(f"{settings_file} is not a JSON object; leaving it untouched.")
                return
        else:
            _warn(f"{settings_file} not found — creating a minimal one")

        hooks_section: dict = settings.setdefault("hooks", {})
        added: list[str] = []
        skipped: list[str] = []

        for event in claude.event_scripts:
            command = _build_managed_command(claude, claude_dir, event)
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
                    f"Update hook {bold(event)} options: "
                    f"{dim(', '.join(f'{k}={v}' for k, v in desired_options.items()))}",
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
            # SEC-105: before the first mutation of a pre-existing settings.json,
            # snapshot it to settings.json.bak so a botched merge is recoverable.
            # Overwrites any prior .bak. The write itself goes through
            # _atomic_write_json (tmp + os.replace, mode-preserving).
            if pre_existing and original_bytes is not None:
                backup = settings_file.with_suffix(settings_file.suffix + ".bak")
                try:
                    backup.write_bytes(original_bytes)
                    # SEC-025: write_bytes creates at umask (often 0644);
                    # the backup mirrors settings.json, which can carry
                    # hooks/env config, so inherit the source file's mode.
                    try:
                        backup.chmod(settings_file.stat().st_mode & 0o777)
                    except OSError:
                        pass
                    _print(
                        dim(f"  Backup of prior settings → {backup}"),
                        verbose_only=True,
                        verbose=verbose,
                    )
                except OSError as exc:
                    # Non-fatal: the atomic write below still protects against
                    # truncation. We just lose the convenience recovery file.
                    _warn(f"Could not write backup {backup}: {exc}")
            try:
                _atomic_write_json(settings_file, settings)
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

    Thin wrapper over the shared ``remove_runtime_hooks`` core (ENH-006).
    Returns True when at least one managed hook registration was found.
    """
    return remove_runtime_hooks(
        _adapter("claude"), settings_file.parent, claude_dir, dry_run
    )


def remove_legacy_hooks(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
) -> bool:
    """Remove managed legacy parsidion-cc hook registrations from settings.json."""
    with _file_lock(settings_file):
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
                _atomic_write_json(settings_file, settings)
                _ok(f"Updated {settings_file}")
            except OSError as exc:
                _err(f"Could not write {settings_file}: {exc}")

        return changed
