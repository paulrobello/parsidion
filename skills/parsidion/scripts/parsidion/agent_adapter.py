"""Shared adapter registry for the agent-extension hook shims (QA-008 / ARC-020).

The five codex/gemini hook scripts (codex_session_start_hook,
gemini_session_start_hook, codex_stop_hook, gemini_session_end_hook,
codex_subagent_stop_hook) were ~90%
copy-paste — 469 lines whose only real variation was three symbols per runtime
(``PARSIDION_RUNTIME``, ``is_<runtime>_transcript_path``, ``parse_<runtime>_transcript_lines``).
Adding a fourth agent then required 2-3 near-identical scripts plus matching
installer copies; the "agent-agnostic" goal was asserted but not yet architected.

This module gives the registry a single home. Each ``AgentAdapter`` is a static
descriptor of one runtime's hook behaviour. Two generic entrypoints —
``run_session_start`` and ``run_session_end`` — carry the shared logic; the
five codex/antigravity scripts reduce to three-line shims that import the relevant
adapter and call the entrypoint.

ARC-002: ``run_session_end`` is now the single session-end pipeline for every
runtime, Claude included — ``session_stop_hook.py`` is a thin shim that adds
Claude's invocation guards (recursion flag, parsight unwatch, verbose
``_should_skip`` checks) and delegates here. The pipeline stages are
adapter-neutral and config-gated: optional AI classification (``--ai`` /
``session_stop_hook.ai_model``), daily-note update, pending-queue append,
``git_commit_vault`` (``git.auto_commit``), auto-summarize launch
(``session_stop_hook.auto_summarize``), and the hook-events entry. The
Codex/Gemini wrappers previously called ``write_hook_event`` and
``git_commit_vault`` **zero** times (vs 2x each in ``session_stop_hook.py``),
so ``vault-stats --hooks`` was blind to every Codex/Gemini session and a
Codex-only user's vault silently accumulated uncommitted daily-note changes.
Centralising makes that gap unrepeatable.

Stdlib only — every consumer (the shims and the installer's hook registration)
is bound by the stdlib-only rule.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from parsidion.core.vault_config import load_config, load_typed_config
from parsidion.core.vault_fs import ensure_vault_dirs
from parsidion.core.vault_hooks import (
    detect_categories,
    get_project_name,
    is_codex_transcript_path,
    is_antigravity_transcript_path,
    is_pi_transcript_path,
    parse_codex_transcript_lines,
    parse_antigravity_transcript_lines,
    parse_transcript_lines,
    write_hook_event,
)
from parsidion.core.vault_path import resolve_vault

# ARC-203: session-end pipeline stage bodies live in ``parsidion.agent_pipeline``;
# the names are re-exported here so ``session_stop_hook``'s
# ``agent_adapter._BACKEND_DEFAULT_AI_MODEL`` reference and test patch
# targets like ``agent_adapter._update_adaptive_scores`` keep working.
# NOTE for patch authors: stage-internal calls bind through
# ``agent_pipeline``'s namespace (e.g. ``_persist_and_report`` ->
# ``_launch_summarizer_if_pending``); patch pipeline-internal callees there.
from parsidion.agent_pipeline import (  # noqa: F401 -- re-exported (ARC-203)
    _BACKEND_DEFAULT_AI_MODEL,
    _DEFAULT_AI_TIMEOUT,
    _DEFAULT_PI_TRANSCRIPT_TAIL_LINES,
    _DEFAULT_SUBAGENT_TAIL_LINES,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
    _MAX_SUMMARY_CHARS,
    _MIN_SUMMARY_LEN,
    _SIGNIFICANT_CATEGORIES,
    _classify_session,
    _classify_session_with_ai,
    _deeper_pi_tail,
    _first_summary,
    _launch_summarizer_if_pending,
    _persist_and_report,
    _read_stdin_payload,
    _read_transcript_tail,
    _resolve_transcript,
    _update_adaptive_scores,
)


# ---------------------------------------------------------------------------
# Adapter descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InstallerSpec:
    """Declarative installer-side description of one runtime (ENH-006/ARC-105).

    The installer reads these to merge/remove hook registrations and write
    instructions files. Held by ``AgentAdapter.install`` — None for runtimes
    with no installer integration (pi/omp are extension-only). Defaults keep
    the dataclass constructible standalone for runtimes that need only a
    subset of the fields.
    """

    display_name: str = ""
    """User-facing label for installer messaging ('Codex', 'Antigravity')."""

    runtime_env_value: str = ""
    """Value set for the PARSIDION_RUNTIME env var when the runtime's hook runs."""

    hooks_config_filename: str | None = None
    """File the runtime stores hook registrations in, relative to its home dir
    ('hooks.json' codex, 'config/hooks.json' antigravity, 'settings.json'
    claude). None for runtimes with no hook config (pi)."""

    event_scripts: dict[str, str] = field(default_factory=dict)
    """Ordered event -> hook-script-filename map (e.g. SessionStart -> codex_session_start_hook.py)."""

    entry_matcher: str = ""
    """``matcher`` value for the hook entry ('' for all current runtimes)."""

    event_matchers: dict[str, str] = field(default_factory=dict)
    """Per-event ``matcher`` overrides when one runtime event needs a tool
    filter others must not carry (codex PreToolUse fires only for file tools;
    the runtime regex-matches the tool name against this value). Events
    absent from the map fall back to :attr:`entry_matcher`."""

    entry_timeout: int = 0
    """Numeric ``timeout`` for the hook entry (paired with ``timeout_unit``)."""

    timeout_unit: Literal["ms", "s"] = "s"
    """Unit of ``entry_timeout`` — 's' (codex/antigravity) or 'ms' (claude). ARC-048a."""

    entry_names: dict[str, str] | None = None
    """Per-event ``name`` values when the runtime's schema requires a name
    (antigravity). None otherwise."""

    instructions_filename: str | None = None
    """Instructions file the installer injects into the runtime home
    ('AGENTS.md' codex, 'GEMINI.md' antigravity). None for claude
    (PARSIDION-VAULT.md, handled separately) and pi."""

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


@dataclass(frozen=True)
class AgentAdapter:
    """Static description of one runtime's hook behaviour.

    Each field names a ``parsidion.core.vault_*`` helper or constant the generic
    entrypoint should call. The dataclass is frozen so adapters can be
    used as dict keys / compared by identity.

    ARC-105: the installer-side declarative data lives on a separate
    ``InstallerSpec`` carried by ``install`` (None for runtimes with no
    installer integration). The former flat fields survive as read-only
    deprecated compat properties below (one release).
    """

    name: str
    """Lowercase runtime identifier — 'codex', 'antigravity', 'pi'."""

    hook_event_name_start: str = ""
    """Hook event name emitted to hook_events.log on session start
    (e.g. 'SessionStart'). Used for observability via vault-stats --hooks."""

    hook_event_name_end: str = ""
    """Hook event name emitted on session end / stop."""

    is_transcript_path: Callable[[Path, str], bool] | None = field(
        default=None, repr=False
    )
    """Optional validator — ``is_codex_transcript_path`` /
    ``is_antigravity_transcript_path``. None for runtimes without a custom
    validator (the entrypoint skips the extra check)."""

    parse_transcript_lines: Callable[[list[str]], list[str]] | None = field(
        default=None, repr=False
    )
    """Optional parser — ``parse_codex_transcript_lines`` /
    ``parse_antigravity_transcript_lines``. None means fall back to the
    shape-agnostic ``parse_transcript_lines``."""

    read_transcript_tail: Callable[[Path, int], list[str]] | None = field(
        default=None, repr=False
    )
    """Optional transcript-tail reader — ``(path, tail_lines) -> lines``.
    None falls back to the shared byte-bounded reader
    ``_read_transcript_tail`` (SEC-022), so every adapter gets the
    ``transcript_tail_bytes`` ceiling regardless of this field."""

    always_log_daily: bool = False
    """When True, append a daily-note session entry even when no categories
    were detected (Claude's native behaviour — a 'General' entry per session).
    Other runtimes only write a daily entry when categories are found."""

    install: InstallerSpec | None = None
    """Installer-side declarative data (ENH-006/ARC-105). None for runtimes
    with no installer integration (pi/omp are extension-only)."""

    # --- ARC-105 deprecated compat shims (remove after one release) ---
    # The ENH-006 fields moved to InstallerSpec; these read-only properties
    # keep flat ``adapter.<field>`` access working (installer/hooks.py,
    # installer/skill.py, external adapters) during the migration window.

    @property
    def display_name(self) -> str:
        """Deprecated compat shim — read ``install.display_name``."""
        return self.install.display_name if self.install is not None else ""

    @property
    def runtime_env_value(self) -> str:
        """Deprecated compat shim — read ``install.runtime_env_value``."""
        return self.install.runtime_env_value if self.install is not None else ""

    @property
    def hooks_config_filename(self) -> str | None:
        """Deprecated compat shim — read ``install.hooks_config_filename``."""
        return self.install.hooks_config_filename if self.install is not None else None

    @property
    def event_scripts(self) -> dict[str, str]:
        """Deprecated compat shim — read ``install.event_scripts``."""
        return self.install.event_scripts if self.install is not None else {}

    @property
    def entry_matcher(self) -> str:
        """Deprecated compat shim — read ``install.entry_matcher``."""
        return self.install.entry_matcher if self.install is not None else ""

    @property
    def entry_timeout(self) -> int:
        """Deprecated compat shim — read ``install.entry_timeout``."""
        return self.install.entry_timeout if self.install is not None else 0

    @property
    def timeout_unit(self) -> Literal["ms", "s"]:
        """Deprecated compat shim — read ``install.timeout_unit``."""
        return self.install.timeout_unit if self.install is not None else "s"

    @property
    def entry_names(self) -> dict[str, str] | None:
        """Deprecated compat shim — read ``install.entry_names``."""
        return self.install.entry_names if self.install is not None else None

    @property
    def instructions_filename(self) -> str | None:
        """Deprecated compat shim — read ``install.instructions_filename``."""
        return self.install.instructions_filename if self.install is not None else None

    @property
    def config_validator(
        self,
    ) -> Callable[[dict[str, object]], dict[str, object] | None] | None:
        """Deprecated compat shim — read ``install.config_validator``."""
        return self.install.config_validator if self.install is not None else None

    @property
    def build_entry(self) -> Callable[[str, str], dict[str, object]] | None:
        """Deprecated compat shim — read ``install.build_entry``."""
        return self.install.build_entry if self.install is not None else None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, AgentAdapter] = {}

# ARC-010: builtin + external adapter registration is lazy. The registry
# starts empty; the first call to ``get`` / ``all_adapters`` /
# ``known_runtimes`` populates the builtins (and, opt-in, any external
# drop-ins from ``~/.config/parsidion/adapters/*.py``). ``register`` does NOT
# trigger the lazy load — that would recurse (``_register_builtin_adapters``
# itself calls ``register``). Tests that need a clean registry call
# ``reset_external_adapters`` (which also forgets builtins so the next
# access reloads them).
_builtins_loaded = False
_external_loaded = False


def register(adapter: AgentAdapter) -> None:
    """Add *adapter* to the registry keyed by its lowercase ``name``.

    Idempotent — re-registering the same name replaces the prior entry so
    tests can pin the registry without polluting production state.

    Does NOT trigger lazy builtin loading (callers within
    ``_register_builtin_adapters`` would otherwise recurse).
    """
    _REGISTRY[adapter.name.lower()] = adapter


def _load_builtin_adapters_if_needed() -> None:
    """Register the builtin runtimes on first access (ARC-010).

    Idempotent. Split from ``_load_external_adapters`` so that ``register``
    can be called before the registry is "warmed up" without recursing.
    """
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True
    _register_builtin_adapters()


def get(name: str) -> AgentAdapter | None:
    """Return the adapter for *name* (case-insensitive), or None."""
    _load_builtin_adapters_if_needed()
    _load_external_adapters()
    return _REGISTRY.get(name.lower())


def all_adapters() -> list[AgentAdapter]:
    """Return every registered adapter. Order is insertion order."""
    _load_builtin_adapters_if_needed()
    _load_external_adapters()
    return list(_REGISTRY.values())


def known_runtimes() -> list[str]:
    """Return the lowercase name of every registered runtime (insertion order)."""
    return [adapter.name for adapter in all_adapters()]


# External adapter loading is opt-in (``adapters.load_external``, default false)
# and runs at most once per process, on first all_adapters()/known_runtimes().
def reset_external_adapters() -> None:
    """Test hook: forget loaded external AND builtin adapters so the next
    access reloads them.

    ARC-010: also resets the builtin-loaded flag so tests that mutated
    ``_REGISTRY`` (e.g. by popping a runtime to simulate its absence) get a
    clean re-population on the next ``get``/``all_adapters`` call.
    """
    global _external_loaded, _builtins_loaded
    _external_loaded = False
    _builtins_loaded = False


def _load_external_adapters() -> None:
    """Opt-in drop-in loader for ``~/.config/parsidion/adapters/*.py``.

    Each file defines a module-level ``ADAPTER: AgentAdapter``. Loading
    arbitrary Python is code execution, so the guards (mirroring SEC-117's
    reasoning for ``codex_cli.command``): off by default; the adapters
    directory and each file must pass the full SEC-007 trust criteria
    (``parsidion.core.vault_fs.is_trusted_executable`` — owned by the current uid, no
    group/world write bits; a group-writable directory would let any group
    member plant a module); every load logged by path. Never raises — a
    broken external adapter must not break the registry or the hooks that
    read it.
    """
    global _external_loaded
    if _external_loaded:
        return
    _external_loaded = True
    try:
        # ARC-101 allowlist: registry population runs before any hook payload
        # (and thus before any vault) exists, so the default-vault read here
        # is deliberate — there is no resolved vault to thread yet.
        if load_typed_config().adapters.load_external is not True:
            return
        import importlib.util

        from parsidion.core.vault_fs import is_trusted_executable

        adapters_dir = Path.home() / ".config" / "parsidion" / "adapters"
        if not adapters_dir.is_dir():
            return
        # SEC-205: gate the directory itself, not just the files — planting a
        # module only needs write access to the dir.
        if not is_trusted_executable(adapters_dir):
            print(
                f"agent_adapter: refusing external adapters from untrusted "
                f"directory {adapters_dir} (not owned by current user or "
                f"group/world-writable)",
                file=sys.stderr,
            )
            return
        for path in sorted(adapters_dir.glob("*.py")):
            # SEC-205: full SEC-007 criteria per file — ownership in addition
            # to the write bits.
            if not is_trusted_executable(path):
                print(
                    f"agent_adapter: refusing untrusted adapter {path} "
                    f"(not owned by current user or group/world-writable)",
                    file=sys.stderr,
                )
                continue
            spec = importlib.util.spec_from_file_location(
                f"_parsidion_adapter_{path.stem}", path
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001 — log and skip, don't raise
                print(f"agent_adapter: failed to load {path}: {exc}", file=sys.stderr)
                continue
            adapter = getattr(module, "ADAPTER", None)
            if isinstance(adapter, AgentAdapter):
                register(adapter)
                print(
                    f"agent_adapter: loaded external adapter {adapter.name!r} from {path}",
                    file=sys.stderr,
                )
    except Exception as exc:  # noqa: BLE001 — external loading never breaks the registry
        print(f"external adapter load failed: {exc}", file=sys.stderr)
        pass


# Event -> hook-script maps. The registry is the single source of truth
# (ENH-006); the duplicates in installer/paths.py are removed once its
# consumers migrate to reading these off the adapter descriptors.
_CLAUDE_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "session_start_hook.py",
    "SessionEnd": "session_stop_wrapper.sh",
    "PreCompact": "pre_compact_hook.py",
    "PostCompact": "post_compact_hook.py",
    "SubagentStop": "subagent_stop_hook.py",
    "UserPromptSubmit": "user_prompt_submit_hook.py",
    "PreToolUse": "pre_tool_use_hook.py",
}
_CODEX_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "codex_session_start_hook.py",
    "Stop": "codex_stop_hook.py",
    "SubagentStop": "codex_subagent_stop_hook.py",
    "UserPromptSubmit": "user_prompt_submit_hook.py",
    # Codex payloads are Claude-shaped (tool_name + tool_input); apply_patch
    # is the one file tool, with its targets inside the V4A patch body under
    # tool_input.command — pre_tool_use_hook.py extracts them itself.
    "PreToolUse": "pre_tool_use_hook.py",
}
# Antigravity defines no UserPromptSubmit event; only claude/codex wire it.
# Codex wires PreToolUse (apply_patch matcher); Antigravity cannot — its PreToolUse
# output is decision/reason only with no context-injection channel (context
# rides PreInvocation/PostInvocation injectSteps per antigravity.google/docs/hooks).
# Session-start maps to PreInvocation (invocationNum==0 gate in the shim),
# session-end to Stop (fullyIdle gate in the shim).
_ANTIGRAVITY_HOOK_SCRIPTS: dict[str, str] = {
    "PreInvocation": "antigravity_session_start_hook.py",
    "Stop": "antigravity_session_end_hook.py",
}
_ANTIGRAVITY_HOOK_NAMES: dict[str, str] = {
    "PreInvocation": "parsidion-session-start",
    "Stop": "parsidion-session-end",
}


def _register_builtin_adapters() -> None:
    """Register the built-in runtimes: claude, codex, antigravity, pi, omp.

    codex/antigravity drive the hook shims via this registry (QA-008/ARC-020) and
    the installer reads their hook-registration data from the same descriptors
    (ENH-006). Since ARC-002, claude's SessionEnd runs the same shared
    pipeline: ``session_stop_hook.py`` is a shim that adds Claude's invocation
    guards (recursion flag, parsight unwatch, verbose skip checks) and calls
    ``run_session_end`` with this adapter; its installer flow still keeps its
    own ``merge_hooks`` for the AI-mode timeout raise. pi and omp ship a
    TypeScript extension that shells out to claude's hook scripts, so they
    carry no hook-registration data (``connect pi`` / ``connect omp`` handle
    the extension copy separately; omp reuses the pi extension source,
    installed into ``$PI_CONFIG_DIR/agent/extensions`` — default
    ``~/.omp/agent/extensions``).
    """
    register(
        AgentAdapter(
            name="claude",
            hook_event_name_start="SessionStart",
            hook_event_name_end="SessionEnd",
            read_transcript_tail=_read_transcript_tail,
            always_log_daily=True,
            install=InstallerSpec(
                display_name="Claude",
                runtime_env_value="claude",
                hooks_config_filename="settings.json",
                event_scripts=_CLAUDE_HOOK_SCRIPTS,
                timeout_unit="ms",
            ),
        )
    )
    register(
        AgentAdapter(
            name="codex",
            hook_event_name_start="CodexSessionStart",
            hook_event_name_end="CodexSessionEnd",
            is_transcript_path=lambda p, cwd: is_codex_transcript_path(p),
            parse_transcript_lines=parse_codex_transcript_lines,
            install=InstallerSpec(
                display_name="Codex",
                runtime_env_value="codex",
                hooks_config_filename="hooks.json",
                event_scripts=_CODEX_HOOK_SCRIPTS,
                entry_matcher="",
                event_matchers={"PreToolUse": "apply_patch"},
                entry_timeout=60,
                timeout_unit="s",
                instructions_filename="AGENTS.md",
            ),
        )
    )
    register(
        AgentAdapter(
            name="antigravity",
            hook_event_name_start="AntigravityPreInvocation",
            hook_event_name_end="AntigravityStop",
            is_transcript_path=lambda p, cwd: is_antigravity_transcript_path(
                p, cwd=cwd
            ),
            parse_transcript_lines=parse_antigravity_transcript_lines,
            install=InstallerSpec(
                display_name="Antigravity",
                runtime_env_value="antigravity",
                hooks_config_filename="config/hooks.json",
                event_scripts=_ANTIGRAVITY_HOOK_SCRIPTS,
                entry_matcher="",
                entry_timeout=60,
                timeout_unit="s",
                entry_names=_ANTIGRAVITY_HOOK_NAMES,
                instructions_filename="GEMINI.md",
            ),
        )
    )
    # pi/omp are extension-only (no installer hook integration): install=None.
    # The flat runtime_env_value read falls back to the adapter name, which is
    # the value these runtimes always used.
    register(AgentAdapter(name="pi"))
    register(AgentAdapter(name="omp"))


# ARC-010: builtins register on first get/all_adapters/known_runtimes call
# (see _load_builtin_adapters_if_needed). Do NOT call
# _register_builtin_adapters() at import time — module side-effects make
# test isolation harder and force every importer (even those that never
# touch the registry) to pay the registration cost.


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
        write_hook_event(
            hook=hook, project=project, duration_ms=0.0, vault=vault, **extra
        )
    except Exception as exc:  # noqa: BLE001
        print(f"hook event emit failed: {exc}", file=sys.stderr)
        pass


# ---------------------------------------------------------------------------
# Session-end pipeline stages (ARC-203)
# ---------------------------------------------------------------------------
#
# The stage bodies live in ``parsidion.agent_pipeline``; this module keeps
# the adapter descriptors, the registry, and the two generic entrypoints
# that orchestrate the stages. The stage names are re-exported at the top
# of this file so the documented stable import surface keeps working.
#
# NOTE for patch authors: stage-internal calls bind through
# ``agent_pipeline``'s namespace (e.g. ``_persist_and_report`` ->
# ``_launch_summarizer_if_pending``). Patch pipeline-internal callees on
# ``parsidion.agent_pipeline``; patching on ``agent_adapter`` still works
# for every call made directly by ``run_session_start`` / ``run_session_end``.


def _to_shared_payload(payload: dict[str, Any]) -> dict[str, object]:
    """Map Antigravity's camelCase wire fields onto the shared pipeline's.

    QA-006: the two Antigravity shim hooks carried byte-identical private
    copies; both now import this one. Lives on the adapter registry (the
    ARC-020 shared layer) rather than a separate antigravity_common module —
    the mapping is the adapter-contract concern.
    """
    workspace_paths = payload.get("workspacePaths")
    cwd = ""
    if isinstance(workspace_paths, list) and workspace_paths:
        cwd = str(workspace_paths[0])
    mapped: dict[str, object] = {}
    if cwd:
        mapped["cwd"] = cwd
    if payload.get("conversationId"):
        mapped["session_id"] = str(payload["conversationId"])
    if payload.get("transcriptPath"):
        mapped["transcript_path"] = str(payload["transcriptPath"])
    return mapped


def run_session_start(
    adapter: AgentAdapter,
    *,
    payload: dict[str, object] | None = None,
    wrap_output: Callable[[str], dict[str, object]] | None = None,
) -> None:
    """SessionStart entrypoint shared across all adapters.

    Reads a JSON payload from stdin (or takes a pre-parsed *payload*, for
    runtimes whose shim must translate the wire format first), builds
    non-AI session context using the Parsidion SessionStart implementation,
    and emits valid JSON for the runtime. All errors are reported to stderr
    while stdout remains valid JSON so the hook never blocks startup.

    *wrap_output* converts the built context string into the runtime's
    output envelope; the default emits the Claude-style
    ``hookSpecificOutput.additionalContext`` shape. Antigravity's shim
    passes an ``injectSteps`` wrapper instead.
    """
    # Lazy import: session_start_hook pulls in vault_search/vault_links and
    # is heavy. Codex/Antigravity session-start is the only path that needs it.
    from session_start_hook import build_session_context

    try:
        if payload is None:
            payload = _read_stdin_payload()

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())

        vault_path = resolve_vault(cwd=cwd)
        max_chars = int(
            load_typed_config(vault=vault_path).session_start_hook.max_chars
        )
        old_runtime = os.environ.get("PARSIDION_RUNTIME")
        os.environ["PARSIDION_RUNTIME"] = adapter.runtime_env_value or adapter.name
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

        output = (
            wrap_output(context)
            if wrap_output is not None
            else {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": context,
                }
            }
        )
        sys.stdout.write(json.dumps(output))
        # ARC-020 step 4: emit a hook event so vault-stats --hooks surfaces
        # Codex/Gemini sessions too.
        try:
            project = get_project_name(cwd)
            _emit_hook_event(
                adapter.hook_event_name_start, project, vault_path, notes_injected=0
            )
        except Exception as exc:  # noqa: BLE001
            print(f"hook event emit failed: {exc}", file=sys.stderr)
            pass
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


def run_session_end(
    adapter: AgentAdapter,
    *,
    subagent: bool = False,
    payload: dict[str, object] | None = None,
    ai_cli_arg: str | None = None,
    final_output: str = "{}",
) -> None:
    """SessionEnd / Stop / SubagentStop entrypoint shared across adapters (ARC-002).

    Validates the runtime's transcript path, parses assistant text via the
    adapter's parser, classifies the session (AI when enabled, keyword
    heuristics otherwise), updates the vault daily note, queues pending
    summarization, commits the vault, and launches the auto-summarizer when
    the queue crosses its threshold. The hook always emits valid JSON on
    stdout and falls back to ``{}`` on errors.

    Args:
        adapter: The runtime's adapter descriptor.
        subagent: When True, treat the payload as a SubagentStop event:
            read ``agent_transcript_path`` instead of ``transcript_path``,
            queue with ``source='subagent'`` + ``agent_type``/``session_id``
            metadata, and skip the daily-note update (subagents fire too
            frequently for daily-note entries to be useful).
        payload: Parsed stdin JSON. When None, stdin is read here (the
            codex/antigravity shims rely on that; Claude's shim pre-parses stdin
            so it can run its own guards first).
        ai_cli_arg: The ``--ai`` argument value from the runtime's CLI, if
            any: ``_BACKEND_DEFAULT_AI_MODEL`` for a bare ``--ai`` (backend
            default model), an explicit model id, or None (no flag). None
            falls back to the ``session_stop_hook.ai_model`` config gate.
    """
    log_prefix = f"[{adapter.name}_session_end]"
    try:
        if payload is None:
            payload = _read_stdin_payload()

        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write(final_output)
            return

        cwd, transcript_path = _resolve_transcript(adapter, payload, subagent)
        if transcript_path is None:
            sys.stdout.write(final_output)
            return

        vault_path = resolve_vault(cwd=cwd)
        ensure_vault_dirs(vault=vault_path)
        project = get_project_name(cwd) if cwd else "unknown"
        hook_start = time.monotonic()

        # Read the transcript tail through the byte-bounded reader. Subagent
        # transcripts are short, so they get a large line ceiling (the byte
        # cap below is the real bound); session transcripts read the
        # configured tail via the adapter's reader (SEC-022/SEC-111).
        # Line budget: the per-hook override when explicitly set, else the
        # unified transcripts.tail_lines (ENH-018).
        _cfg = load_typed_config(vault=vault_path)
        _raw_ssh = load_config(vault=vault_path).get("session_stop_hook") or {}
        _lines_override = _raw_ssh.get("transcript_tail_lines")
        if subagent:
            tail_lines = _DEFAULT_SUBAGENT_TAIL_LINES
        elif _lines_override is None:
            tail_lines = int(_cfg.transcripts.tail_lines)
        else:
            tail_lines = int(_cfg.session_stop_hook.transcript_tail_lines)
        raw_lines = (
            _read_transcript_tail(transcript_path, tail_lines, vault=vault_path)
            if subagent
            else (
                adapter.read_transcript_tail(transcript_path, tail_lines)
                if adapter.read_transcript_tail is not None
                else _read_transcript_tail(
                    transcript_path, tail_lines, vault=vault_path
                )
            )
        )

        if adapter.parse_transcript_lines is not None:
            parse_lines = adapter.parse_transcript_lines
        else:
            parse_lines = parse_transcript_lines
        assistant_texts = parse_lines(raw_lines)

        # pi transcripts can be noisier than Claude tails — when the default
        # tail found no assistant text, read a deeper tail and re-parse.
        if (
            not subagent
            and not assistant_texts
            and is_pi_transcript_path(transcript_path, cwd=cwd)
        ):
            deeper = _deeper_pi_tail(transcript_path, tail_lines, vault=vault_path)
            if deeper is not None:
                raw_lines = deeper
                assistant_texts = parse_lines(raw_lines)

        if not subagent:
            # Adaptive context: update usefulness scores before we do
            # anything else (config-gated, best-effort).
            _update_adaptive_scores(project, raw_lines, log_prefix, vault=vault_path)

        if not assistant_texts:
            print(
                f"{log_prefix} skipping: no assistant messages found in "
                "transcript tail",
                file=sys.stderr,
            )
            sys.stdout.write(final_output)
            return

        print(
            f"{log_prefix} parsed {len(assistant_texts)} assistant message(s)",
            file=sys.stderr,
        )

        # Classify once (QA-002): (categories, summary, queued, force, mode).
        # AI first (CLI flag / config gated, session events only); keyword
        # heuristics are the fallback.
        classified = (
            _classify_session(
                assistant_texts, project, ai_cli_arg, log_prefix, vault=vault_path
            )
            if not subagent
            else None
        )
        if classified is None:
            categories = detect_categories(assistant_texts)
            cats_str = ", ".join(categories.keys()) or "none"
            print(
                f"{log_prefix} keyword detection: categories=[{cats_str}]",
                file=sys.stderr,
            )
            queued = bool(_SIGNIFICANT_CATEGORIES & set(categories.keys()))
            # append_to_pending applies the same significance filter itself.
            classified = (
                categories,
                _first_summary(assistant_texts),
                queued,
                False,
                "keyword",
            )

        categories, summary, queued, force_queue, mode = classified
        _persist_and_report(
            adapter,
            vault_path=vault_path,
            project=project,
            transcript_path=transcript_path,
            categories=categories,
            first_summary=summary,
            queued=queued,
            force_queue=force_queue,
            mode=mode,
            hook_start=hook_start,
            subagent=subagent,
            payload=payload,
        )

        sys.stdout.write(final_output)
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write(final_output)
