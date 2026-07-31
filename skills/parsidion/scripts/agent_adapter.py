"""Shared adapter registry for the agent-extension hook shims (QA-008 / ARC-020).

The five codex/gemini hook scripts (codex_session_start_hook, gemini_session_start_hook,
codex_stop_hook, gemini_session_end_hook, codex_subagent_stop_hook) were ~90%
copy-paste — 469 lines whose only real variation was three symbols per runtime
(``PARSIDION_RUNTIME``, ``is_<runtime>_transcript_path``, ``parse_<runtime>_transcript_lines``).
Adding a fourth agent today requires 2-3 near-identical scripts plus matching
installer copies; the "agent-agnostic" goal is asserted but not architected.

This module gives the registry a single home. Each ``AgentAdapter`` is a static
descriptor of one runtime's hook behaviour. Two generic entrypoints —
``run_session_start`` and ``run_session_end`` — carry the shared logic; the
five codex/gemini scripts reduce to three-line shims that import the relevant
adapter and call the entrypoint.

The entrypoints deliberately fold ``write_hook_event`` and ``git_commit_vault``
into the shared path. The Codex/Gemini wrappers previously called each **zero**
times (vs 2× each in ``session_stop_hook.py``), so ``vault-stats --hooks`` was
blind to every Codex/Gemini session and a Codex-only user's vault silently
accumulated uncommitted daily-note changes. Centralising makes that gap
unrepeatable.

Stdlib only — every consumer (the shims and the installer's hook registration)
is bound by the stdlib-only rule.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import vault_common

# ---------------------------------------------------------------------------
# Adapter descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentAdapter:
    """Static description of one runtime's hook behaviour.

    Each field names a vault_common helper or constant the generic
    entrypoint should call. The dataclass is frozen so adapters can be
    used as dict keys / compared by identity.
    """

    name: str
    """Lowercase runtime identifier — 'codex', 'gemini', 'pi'."""

    hook_event_name_start: str = ""
    """Hook event name emitted to hook_events.log on session start
    (e.g. 'SessionStart'). Used for observability via vault-stats --hooks."""

    hook_event_name_end: str = ""
    """Hook event name emitted on session end / stop."""

    is_transcript_path: Callable[[Path, str], bool] | None = field(
        default=None, repr=False
    )
    """Optional validator — ``is_codex_transcript_path`` /
    ``is_gemini_transcript_path``. None for runtimes without a custom
    validator (the entrypoint skips the extra check)."""

    parse_transcript_lines: Callable[[list[str]], list[str]] | None = field(
        default=None, repr=False
    )
    """Optional parser — ``parse_codex_transcript_lines`` /
    ``parse_gemini_transcript_lines``. None means fall back to the
    shape-agnostic ``vault_common.parse_transcript_lines``."""

    # --- ENH-006: installer-side declarative fields ---
    # The installer reads these to merge/remove hook registrations and write
    # instructions files, so one descriptor covers both the hook shims and the
    # installer. Defaults keep the dataclass constructible for runtimes that
    # only need a subset (e.g. pi uses none of the installer-hook fields).
    display_name: str = ""
    """User-facing label for installer messaging ('Codex', 'Gemini')."""

    runtime_env_value: str = ""
    """Value set for the PARSIDION_RUNTIME env var when the runtime's hook runs."""

    hooks_config_filename: str | None = None
    """File the runtime stores hook registrations in, relative to its home dir
    ('hooks.json' codex, 'settings.json' gemini/claude). None for runtimes with
    no hook config (pi)."""

    event_scripts: dict[str, str] = field(default_factory=dict)
    """Ordered event -> hook-script-filename map (e.g. SessionStart -> codex_session_start_hook.py)."""

    entry_matcher: str = ""
    """``matcher`` value for the hook entry ('' codex, '*' gemini/claude)."""

    entry_timeout: int = 0
    """Numeric ``timeout`` for the hook entry (paired with ``timeout_unit``)."""

    timeout_unit: Literal["ms", "s"] = "s"
    """Unit of ``entry_timeout`` — 's' (codex) or 'ms' (gemini/claude). ARC-048a."""

    entry_names: dict[str, str] | None = None
    """Per-event ``name`` values when the runtime's schema requires a name
    (gemini). None otherwise."""

    instructions_filename: str | None = None
    """Instructions file the installer injects into the runtime home
    ('AGENTS.md' codex, 'GEMINI.md' gemini). None for claude (CLAUDE-VAULT.md,
    handled separately) and pi."""

    config_validator: Callable[[dict[str, object]], dict[str, object] | None] | None = (
        field(default=None, repr=False)
    )
    """Pure per-runtime JSON-shape check on the loaded hook config: returns the
    config dict when safe to edit, None when not. Installer-supplied at
    migration time; a fact about the runtime, not about the installer."""

    build_entry: Callable[[str, str], dict[str, object]] | None = field(
        default=None, repr=False
    )
    """Optional entry-builder override for runtimes whose entry needs logic, not
    just data (Claude's AI-mode timeout raise). When None the installer builds
    the entry from ``entry_matcher``/``entry_timeout``/``entry_names``."""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, AgentAdapter] = {}


def register(adapter: AgentAdapter) -> None:
    """Add *adapter* to the registry keyed by its lowercase ``name``.

    Idempotent — re-registering the same name replaces the prior entry so
    tests can pin the registry without polluting production state.
    """
    _REGISTRY[adapter.name.lower()] = adapter


def get(name: str) -> AgentAdapter | None:
    """Return the adapter for *name* (case-insensitive), or None."""
    return _REGISTRY.get(name.lower())


def all_adapters() -> list[AgentAdapter]:
    """Return every registered adapter. Order is insertion order."""
    return list(_REGISTRY.values())


def known_runtimes() -> list[str]:
    """Return the lowercase name of every registered runtime (insertion order)."""
    return [adapter.name for adapter in _REGISTRY.values()]


# Event -> hook-script maps. The registry is the single source of truth
# (ENH-006); the duplicates in installer/paths.py are removed once its
# consumers migrate to reading these off the adapter descriptors.
_CLAUDE_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "session_start_hook.py",
    "SessionEnd": "session_stop_wrapper.sh",
    "PreCompact": "pre_compact_hook.py",
    "PostCompact": "post_compact_hook.py",
    "SubagentStop": "subagent_stop_hook.py",
}
_CODEX_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "codex_session_start_hook.py",
    "Stop": "codex_stop_hook.py",
    "SubagentStop": "codex_subagent_stop_hook.py",
}
_GEMINI_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "gemini_session_start_hook.py",
    "SessionEnd": "gemini_session_end_hook.py",
}
_GEMINI_HOOK_NAMES: dict[str, str] = {
    "SessionStart": "parsidion-session-start",
    "SessionEnd": "parsidion-session-end",
}


def _register_builtin_adapters() -> None:
    """Register the built-in runtimes: claude, codex, gemini, pi.

    codex/gemini drive the hook shims via this registry (QA-008/ARC-020) and
    the installer reads their hook-registration data from the same descriptors
    (ENH-006). claude's native hooks predate the registry; it is registered
    for installer completeness (``known_runtimes``/``connect``) and a single
    observability naming convention — its native hook scripts keep running as
    before. pi ships a TypeScript extension that shells out to claude's hook
    scripts, so it carries no hook-registration data (``connect pi`` handles
    the extension copy separately).
    """
    register(
        AgentAdapter(
            name="claude",
            display_name="Claude",
            runtime_env_value="claude",
            hook_event_name_start="SessionStart",
            hook_event_name_end="SessionEnd",
            hooks_config_filename="settings.json",
            event_scripts=_CLAUDE_HOOK_SCRIPTS,
            timeout_unit="ms",
        )
    )
    register(
        AgentAdapter(
            name="codex",
            display_name="Codex",
            runtime_env_value="codex",
            hook_event_name_start="CodexSessionStart",
            hook_event_name_end="CodexSessionEnd",
            is_transcript_path=lambda p, cwd: vault_common.is_codex_transcript_path(p),
            parse_transcript_lines=vault_common.parse_codex_transcript_lines,
            hooks_config_filename="hooks.json",
            event_scripts=_CODEX_HOOK_SCRIPTS,
            entry_matcher="",
            entry_timeout=60,
            timeout_unit="s",
            instructions_filename="AGENTS.md",
        )
    )
    register(
        AgentAdapter(
            name="gemini",
            display_name="Gemini",
            runtime_env_value="gemini",
            hook_event_name_start="GeminiSessionStart",
            hook_event_name_end="GeminiSessionEnd",
            is_transcript_path=lambda p, cwd: vault_common.is_gemini_transcript_path(
                p, cwd=cwd
            ),
            parse_transcript_lines=vault_common.parse_gemini_transcript_lines,
            hooks_config_filename="settings.json",
            event_scripts=_GEMINI_HOOK_SCRIPTS,
            entry_matcher="*",
            entry_timeout=10000,
            timeout_unit="ms",
            entry_names=_GEMINI_HOOK_NAMES,
            instructions_filename="GEMINI.md",
        )
    )
    register(
        AgentAdapter(
            name="pi",
            display_name="pi",
            runtime_env_value="pi",
        )
    )


_register_builtin_adapters()


# ---------------------------------------------------------------------------
# Generic entrypoints
# ---------------------------------------------------------------------------


def _emit_hook_event(hook: str, project: str, vault: Path, **extra: object) -> None:
    """Best-effort write_hook_event + git_commit_vault.

    Both calls are best-effort (swallow OSError) so a failure in the
    observability layer cannot break the user's session. This is the gap
    ARC-020 step 4 closes: the codex/gemini wrappers previously did NOT
    emit hook events or commit the daily note, leaving ``vault-stats --hooks``
    blind to every Codex/Gemini session.
    """
    try:
        vault_common.write_hook_event(
            hook=hook, project=project, duration_ms=0.0, vault=vault, **extra
        )
    except Exception:  # noqa: BLE001
        pass


def _first_summary(texts: list[str]) -> str:
    """Return a compact summary candidate from parsed assistant text."""
    for text in texts:
        if len(text.strip()) > 50:
            return text[:500]
    return texts[0][:500] if texts else ""


def run_session_start(adapter: AgentAdapter) -> None:
    """SessionStart entrypoint shared across all adapters.

    Reads a JSON payload from stdin, builds non-AI session context using the
    Parsidion SessionStart implementation, and emits valid JSON for the
    runtime. All errors are reported to stderr while stdout remains valid
    JSON so the hook never blocks startup.
    """
    # Lazy import: session_start_hook pulls in vault_search/vault_links and
    # is heavy. Codex/Gemini session-start is the only path that needs it.
    from session_start_hook import _DEFAULT_MAX_CHARS, build_session_context

    try:
        payload: dict[str, object] = {}
        try:
            raw = sys.stdin.read() or "{}"
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            payload = {}

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())

        max_chars = int(
            vault_common.get_config(
                "session_start_hook", "max_chars", _DEFAULT_MAX_CHARS
            )
        )
        old_runtime = os.environ.get("PARSIDION_RUNTIME")
        os.environ["PARSIDION_RUNTIME"] = adapter.name
        try:
            context, _notes_injected = build_session_context(
                cwd,
                ai_model=None,
                max_chars=max_chars,
                verbose_mode=False,
            )
        finally:
            if old_runtime is None:
                os.environ.pop("PARSIDION_RUNTIME", None)
            else:
                os.environ["PARSIDION_RUNTIME"] = old_runtime

        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": context,
                    }
                }
            )
        )
        # ARC-020 step 4: emit a hook event so vault-stats --hooks surfaces
        # Codex/Gemini sessions too.
        try:
            vault_path = vault_common.resolve_vault(cwd=cwd)
            project = vault_common.get_project_name(cwd)
            _emit_hook_event(
                adapter.hook_event_name_start, project, vault_path, notes_injected=0
            )
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


def run_session_end(adapter: AgentAdapter, *, subagent: bool = False) -> None:
    """SessionEnd / Stop / SubagentStop entrypoint shared across adapters.

    Validates the runtime's transcript path, parses assistant text via the
    adapter's parser, updates the vault daily note, and queues pending
    summarization when useful categories are detected. The hook always emits
    valid JSON on stdout and falls back to ``{}`` on errors.

    Args:
        adapter: The runtime's adapter descriptor.
        subagent: When True, treat the payload as a SubagentStop event:
            read ``agent_transcript_path`` instead of ``transcript_path``,
            queue with ``source='subagent'`` + ``agent_type``/``session_id``
            metadata, and skip the daily-note update (subagents fire too
            frequently for daily-note entries to be useful).
    """
    try:
        try:
            raw = sys.stdin.read() or "{}"
            parsed = json.loads(raw)
            payload: dict[str, object] = parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            payload = {}

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())

        transcript_key = "agent_transcript_path" if subagent else "transcript_path"
        transcript_value = payload.get(transcript_key)
        if not transcript_value:
            sys.stdout.write("{}")
            return

        transcript_path = Path(str(transcript_value))
        if not transcript_path.is_file():
            sys.stdout.write("{}")
            return

        if not vault_common.is_allowed_transcript_path(transcript_path, cwd=cwd):
            sys.stdout.write("{}")
            return
        if adapter.is_transcript_path is not None and not adapter.is_transcript_path(
            transcript_path, cwd
        ):
            sys.stdout.write("{}")
            return

        vault_path = vault_common.resolve_vault(cwd=cwd)
        vault_common.ensure_vault_dirs(vault=vault_path)

        if subagent:
            # Read ALL lines — subagent transcripts are short.
            try:
                with open(transcript_path, encoding="utf-8", errors="replace") as fh:
                    raw_lines = fh.readlines()
            except OSError as exc:
                print(
                    f"[{adapter.name}_subagent_stop] ERROR reading transcript: {exc}",
                    file=sys.stderr,
                )
                sys.stdout.write("{}")
                return
        else:
            tail_lines = int(
                vault_common.get_config(
                    "session_stop_hook", "transcript_tail_lines", 200
                )
            )
            raw_lines = vault_common.read_last_n_lines(transcript_path, tail_lines)

        if adapter.parse_transcript_lines is not None:
            assistant_texts = adapter.parse_transcript_lines(raw_lines)
        else:
            assistant_texts = vault_common.parse_transcript_lines(raw_lines)
        if not assistant_texts:
            sys.stdout.write("{}")
            return

        categories = vault_common.detect_categories(assistant_texts)
        project = vault_common.get_project_name(cwd)

        if subagent:
            agent_id = str(payload.get("agent_id") or "") or None
            agent_type = str(payload.get("agent_type") or "") or None
            vault_common.append_to_pending(
                transcript_path=transcript_path,
                project=project,
                categories=categories,
                source="subagent",
                agent_type=agent_type,
                session_id=agent_id,
                vault=vault_path,
            )
        elif categories:
            vault_common.append_session_to_daily(
                project,
                categories,
                _first_summary(assistant_texts),
                vault_path,
            )
            vault_common.append_to_pending(
                transcript_path,
                project,
                categories,
                vault=vault_path,
            )

        # ARC-020 step 4: emit a hook event so vault-stats --hooks surfaces
        # this runtime's sessions, AND commit the daily note change so a
        # Codex-only user's vault doesn't accumulate uncommitted changes.
        _emit_hook_event(
            adapter.hook_event_name_end
            if not subagent
            else f"{adapter.name.title()}SubagentStop",
            project,
            vault_path,
            runtime=adapter.name,
            source="subagent" if subagent else "session",
            **{"categories": {k: len(v) for k, v in categories.items()}},
        )

        sys.stdout.write("{}")
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")
