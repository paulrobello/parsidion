# Agent Adapters

Parsidion is **agent-agnostic**: the same vault, hooks, and installer serve multiple coding-agent
runtimes (Claude Code, Codex CLI, Antigravity (`agy`), pi, omp, and third-party runtimes). The mechanism is a
single registry of **`AgentAdapter`** descriptors — one per runtime — that the hook shims, the
installer, and `connect`/`disconnect` all read from. Adding a runtime means describing it against
this contract rather than copying the hook scripts.

The registry lives in [`skills/parsidion/scripts/agent_adapter.py`](../skills/parsidion/scripts/agent_adapter.py)
and is stdlib-only (it is imported by both the hook shims and the installer, both of which are bound
by the [stdlib-only rule](../CLAUDE.md)).

## Table of Contents

- [The `AgentAdapter` contract](#the-agentadapter-contract)
- [Built-in runtimes](#built-in-runtimes)
- [Adding a runtime](#adding-a-runtime)
- [External adapters](#external-adapters)
- [Timeout units](#timeout-units)
- [Stdlib-only rule](#stdlib-only-rule)
- [Architecture notes](#architecture-notes)
- [Related documentation](#related-documentation)

## The `AgentAdapter` contract

Each field names one thing that varies between runtimes. The hook shims drive per-runtime behaviour
from the runtime fields; the installer's generic hook-registration core reads the adapter's
**`InstallerSpec`** (carried by `install`, ARC-105). A field exists only when ≥2 runtimes use it or
a runtime's schema requires it (one-runtime behaviour stays a callable override, not a field).

### `AgentAdapter` (runtime) fields

| Group | Field | Meaning |
|---|---|---|
| **Identity** | `name` | Lowercase runtime id (`claude`, `codex`, `antigravity`, `pi`, …). Registry key. |
| **Hook side** | `hook_event_name_start` / `hook_event_name_end` | Names emitted to `hook_events.log` for `vault-stats --hooks` observability. |
| | `is_transcript_path` | Optional `(path, cwd) -> bool` validator for the runtime's transcript files. `None` skips the check. |
| | `parse_transcript_lines` | Optional `(lines) -> [str]` parser for assistant text. `None` falls back to the shape-agnostic parser. |
| | `read_transcript_tail` | Optional `(path, tail_lines) -> [str]` transcript-tail reader. `None` falls back to the shared byte-bounded reader (`transcript_tail_bytes` ceiling, SEC-022). |
| | `always_log_daily` | When `true`, write a daily-note session entry even with no detected categories (Claude's 'General' entry). Default `false` — daily entries only when categories are found. |
| **Installer** | `install` | The runtime's `InstallerSpec` (below). `None` for runtimes with no installer integration (pi, omp are extension-only). |

### `InstallerSpec` fields

Held by `AgentAdapter.install`; a standalone frozen dataclass so the installer helpers can take
`(spec, runtime_name)` without the rest of the adapter.

| Group | Field | Meaning |
|---|---|---|
| **Identity** | `display_name` | User-facing label for installer messages (`Codex`). |
| | `runtime_env_value` | Value set for `PARSIDION_RUNTIME` when the runtime's hook runs. |
| **Hook registration** | `hooks_config_filename` | File the runtime stores hooks in, relative to its home (`hooks.json`, `settings.json`). `None` = no hook config (pi, omp). |
| | `event_scripts` | Ordered `event -> hook-script-filename` map (e.g. `SessionStart -> codex_session_start_hook.py`). |
| | `entry_matcher` | the `matcher` for the hook entry (always `""` for codex/claude/antigravity). |
| | `entry_timeout` + `timeout_unit` | Numeric timeout and its unit — **`"s"` (codex/antigravity) or `"ms"` (claude)**. See [Timeout units](#timeout-units). |
| | `entry_names` | Per-event `name` values when the runtime's schema requires one (antigravity). `None` otherwise. |
| | `config_validator` | Optional pure `(dict) -> dict | None` JSON-shape check on the loaded hook config (`None` = unsafe to edit). Reserved: no built-in sets it — the installer's `_read_runtime_hooks` validates inline. |
| | `build_entry` | Optional `(event, command) -> dict` override for entries that need logic, not just data. Reserved: no built-in sets it — the installer's `_build_entry` builds every entry from `entry_matcher`/`entry_timeout`/`entry_names`. |
| **Instructions** | `instructions_filename` | File the installer injects agent instructions into (`AGENTS.md`, `GEMINI.md`). `None` for claude (uses `PARSIDION-VAULT.md`) and pi/omp. |

### Deprecated flat read-properties

The eleven `InstallerSpec` field names survive on `AgentAdapter` as **read-only properties** that
delegate to `install` (returning each field's old default — `None`/`""`/`{}`/`0`/`"s"` — when
`install` is `None`). This is a one-release compat shim so `adapter.event_scripts`,
`adapter.instructions_filename`, … keep reading correctly at unchanged call sites; setting any of
them raises `AttributeError`. New code should read `adapter.install.<field>` (and construct via
`install=InstallerSpec(...)`).

### Migration for external adapter authors

Before ARC-105 the installer fields were set directly on the `AgentAdapter` constructor. If your
`~/.config/parsidion/adapters/*.py` module passes any of the eleven spec fields
(`display_name`, `runtime_env_value`, `hooks_config_filename`, `event_scripts`, `entry_matcher`,
`entry_timeout`, `timeout_unit`, `entry_names`, `instructions_filename`, `config_validator`,
`build_entry`) to `AgentAdapter(...)`, move them into an `InstallerSpec`:

```python
from agent_adapter import AgentAdapter, InstallerSpec

ADAPTER = AgentAdapter(
    name="acme",
    hook_event_name_start="AcmeSessionStart",
    hook_event_name_end="AcmeSessionEnd",
    install=InstallerSpec(
        display_name="Acme",
        runtime_env_value="acme",
        hooks_config_filename="hooks.json",
        event_scripts={"SessionStart": "acme_session_start_hook.py"},
    ),
)
```

Flat reads (`adapter.display_name`) keep working during the deprecation window; flat constructor
arguments do not.

## Built-in runtimes

Registered lazily by `_register_builtin_adapters()` — the registry populates on the first
`get`/`all_adapters`/`known_runtimes()` call, not at module import (ARC-010 keeps import-time side
effects at zero):

| Runtime | Hooks | Connect path | Notes |
|---|---|---|---|
| `claude` | `settings.json` | `install()`/`uninstall()` (native hooks) | Keeps its own `merge_hooks` flow (unified 60 s SessionStart timeout raise via `installer.paths._HOOK_OPTIONS`, update-existing-options, SEC-105 `.bak` snapshot); reads `event_scripts` from the adapter. Since ARC-002, `session_stop_hook.py` is a shim over `run_session_end` with this adapter (`read_transcript_tail` byte-bounded reader, `always_log_daily=true`). |
| `codex` | `~/.codex/hooks.json` | `install()`/`uninstall()` | Generic `_merge_runtime_hooks` / `remove_runtime_hooks`. Timeout in **seconds**. |
| `antigravity` | `~/.gemini/config/hooks.json` | `install()`/`uninstall()` | Generic core. Named hooks (`parsidion-session-start` / `parsidion-session-end`) use `PreInvocation` + `Stop`, with empty matchers and 60-second timeouts. The `agy` binary receives session-start context as an `ephemeralMessage` via `injectSteps`; transcripts are under `~/.gemini/antigravity-cli/brain/<conversationId>/.system_generated/logs/transcript.jsonl`. `GEMINI.md` remains the instructions file. |

| `pi` | none | `connect pi` runs `scripts/install-pi-extension` | Extension-only: ships a TypeScript extension that shells out to claude's hook scripts at runtime (preferring `uv run --no-project`). |
| `omp` | none | `connect omp` runs `scripts/install-pi-extension --extension-dir <omp-home>/agent/extensions --agent-name omp` | Extension-only, same source as pi. omp resolves its agent dir from `$PI_CONFIG_DIR` (default `~/.omp`); `--omp-home` overrides. omp's extension loader resolves the extension's `@mariozechner/*` imports and emits every event it uses; subagent capture is a no-op under omp (no `subagent:result` messages). |

Antigravity uses named hooks in `~/.gemini/config/hooks.json` (global), with this shape:

```json
{"<hook-name>": {"<Event>": [{"matcher": "", "hooks": [{"type": "command", "command": "...", "timeout": 60}]}]}}
```

The supported events are `PreInvocation`, `PostInvocation`, `PreToolUse`, `PostToolUse`, and `Stop`; there are no `SessionStart` or `SessionEnd` events. Parsidion maps session start to `PreInvocation` and session end to `Stop`.

## Adding a runtime

**Hooks-only runtime** (the common case): add one `AgentAdapter` to `_register_builtin_adapters()`
(or drop a file in the [external dir](#external-adapters)). Because `event_scripts`, the entry shape,
and the config filename are all descriptor fields, the generic `_merge_runtime_hooks` /
`remove_runtime_hooks` core serves it with no new hook scripts, and the runtime appears automatically
in `install.py connect`/`disconnect` choices and `known_runtimes()`. The install/uninstall plans
still gate hook merging on named per-runtime flags (`_wants_codex_runtime`, …) that call thin
wrappers (`merge_codex_hooks`, `remove_antigravity_hooks`, …), so wiring the new runtime into `install()`
is one small wrapper plus a plan flag.

```python
register(
    AgentAdapter(
        name="acme",
        hook_event_name_start="AcmeSessionStart",
        hook_event_name_end="AcmeSessionEnd",
        install=InstallerSpec(
            display_name="Acme",
            runtime_env_value="acme",
            hooks_config_filename="hooks.json",
            event_scripts={"SessionStart": "acme_session_start_hook.py"},
            entry_matcher="",
            entry_timeout=60,
            timeout_unit="s",
            instructions_filename="AGENTS.md",
        ),
    )
)
```

Runtimes whose install needs more than hook registration (like pi's extension copy) keep a small,
named branch in the `connect`/`disconnect` dispatch — the rule of thumb is: if only one runtime needs
a behaviour, it does **not** become a descriptor field.

## External adapters

Third-party runtimes can be added without forking: drop a `*.py` file in
`~/.config/parsidion/adapters/` defining a module-level `ADAPTER: AgentAdapter`, and enable
`adapters.load_external: true` in `config.yaml`. The loader (`_load_external_adapters`) runs once on
first registry access.

Loading arbitrary Python is **code execution**, so three guards (mirroring the SEC-117 reasoning for
`codex_cli.command`):

1. **Off by default.** `adapters.load_external` is `false` unless explicitly set.
2. **Permission check.** The adapters directory itself and each `*.py` file must pass the full
   SEC-007 trust check (`vault_fs.is_trusted_executable`): owned by the current user, with no
   group- or world-writable bits (SEC-205 — planting a module only needs write access to the dir).
3. **Load-time logging.** Every externally-loaded adapter (and every refusal/failure) is logged to
   stderr by path.

Never raises — a broken external adapter cannot break the registry or the hooks that read it.

## Timeout units

The `timeout_unit` field exists because the runtimes disagree: Codex's `hooks.json` `timeout` is in
**seconds** (`Duration::from_secs` in codex-rs), while Claude's `settings.json` uses **milliseconds**. Stating the unit in the descriptor makes it explicit and
checkable instead of an undocumented literal (audit item ARC-048a identified an undocumented legacy timeout scale).

## Stdlib-only rule

`agent_adapter.py` and every hook shim import nothing outside the Python standard library (plus the
stdlib-only `vault_common`). This is the project's hardest constraint and it is enforced by
`tests/test_stdlib_only.py`, which imports every `core/*` module, every hook, and the adapter
registry itself in a fresh interpreter with 12 third-party packages poisoned in `sys.modules`
(`rich`, `fastembed`, `sqlite_vec`, `anyio`, `yaml`, `numpy`, `PIL`, `requests`, `aiohttp`, plus
their alias spellings). Any adapter descriptor that pulls a third-party import — even transitively —
fails the gate.

## Architecture notes

- **One descriptor, two consumers.** The hook shims (`codex_session_start_hook.py`, …) call
  `run_session_start`/`run_session_end` with their adapter; the installer's `_merge_runtime_hooks` /
  `remove_runtime_hooks` take the same adapter and read its `InstallerSpec`
  (`adapter.install`) — the purely installer-side helpers (`_runtime_hooks_file`,
  `_read_runtime_hooks`, `_build_managed_command`, `_build_entry`) type their parameters as
  `InstallerSpec`, plus the runtime name where a message needs it (`_runtime_hooks_file`,
  `_read_runtime_hooks`) (ARC-105).
- **Installer → scripts dependency.** The installer imports `agent_adapter` from
  `skills/parsidion/scripts/`. `installer/__init__.py` puts that directory on `sys.path` at package
  import (established precedent — `installer/paths.py` already imports `core.vault_path` the same
  way).
  This is safe because `vault_common` is stdlib-only at import time.
- **Generic core.** `_merge_runtime_hooks(adapter, …)` and `remove_runtime_hooks(adapter, …)` in
  `installer/hooks.py` are the single read-modify-write path for codex/antigravity (and any future
  hooks-based runtime). Claude retains its own `merge_hooks` for its options-raise/`.bak` flow but
  reads `event_scripts` and builds commands through the shared helpers.

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system architecture and hook lifecycle.
- [SECURITY.md](../SECURITY.md) — the hook-execution and installer security surface (runtimes + scope table).
- [CLAUDE.md](../CLAUDE.md) — the runtime-adapter hook components and the stdlib-only rule.
