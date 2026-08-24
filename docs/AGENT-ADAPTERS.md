# Agent Adapters

Parsidion is **agent-agnostic**: the same vault, hooks, and installer serve multiple coding-agent
runtimes (Claude Code, Codex CLI, Gemini CLI, pi, omp, and third-party runtimes). The mechanism is a
single registry of **`AgentAdapter`** descriptors — one per runtime — that the hook shims, the
installer, and `connect`/`disconnect` all read from. Adding a runtime is a data-only change against
this contract rather than a copy-the-scripts ritual.

The registry lives in [`skills/parsidion/scripts/agent_adapter.py`](../skills/parsidion/scripts/agent_adapter.py)
and is stdlib-only (it is imported by both the hook shims and the installer, both of which are bound
by the [stdlib-only rule](../CLAUDE.md)).

## The `AgentAdapter` contract

Each field names one thing that varies between runtimes. The installer drives all per-runtime
behaviour from these; a field exists only when ≥2 runtimes use it or a runtime's schema requires it
(one-runtime behaviour stays a callable override, not a field).

| Group | Field | Meaning |
|---|---|---|
| **Identity** | `name` | Lowercase runtime id (`claude`, `codex`, `gemini`, `pi`, …). Registry key. |
| | `display_name` | User-facing label for installer messages (`Codex`). |
| | `runtime_env_value` | Value set for `PARSIDION_RUNTIME` when the runtime's hook runs. |
| **Hook side** | `hook_event_name_start` / `hook_event_name_end` | Names emitted to `hook_events.log` for `vault-stats --hooks` observability. |
| | `is_transcript_path` | Optional `(path, cwd) -> bool` validator for the runtime's transcript files. `None` skips the check. |
| | `parse_transcript_lines` | Optional `(lines) -> [str]` parser for assistant text. `None` falls back to the shape-agnostic parser. |
| | `read_transcript_tail` | Optional `(path, tail_lines) -> [str]` transcript-tail reader. `None` falls back to the shared byte-bounded reader (`transcript_tail_bytes` ceiling, SEC-022). |
| | `always_log_daily` | When `true`, write a daily-note session entry even with no detected categories (Claude's 'General' entry). Default `false` — daily entries only when categories are found. |
| **Hook registration** | `hooks_config_filename` | File the runtime stores hooks in, relative to its home (`hooks.json`, `settings.json`). `None` = no hook config (pi). |
| | `event_scripts` | Ordered `event -> hook-script-filename` map (e.g. `SessionStart -> codex_session_start_hook.py`). |
| | `entry_matcher` | `matcher` for the hook entry (`""` codex/claude, `"*"` gemini). |
| | `entry_timeout` + `timeout_unit` | Numeric timeout and its unit — **`"s"` (codex) or `"ms"` (gemini/claude)**. See [Timeout units](#timeout-units). |
| | `entry_names` | Per-event `name` values when the runtime's schema requires one (gemini). `None` otherwise. |
| | `config_validator` | Optional pure `(dict) -> dict | None` JSON-shape check on the loaded hook config (`None` = unsafe to edit). |
| | `build_entry` | Optional `(event, command) -> dict` override for entries that need logic, not just data (Claude's AI-mode timeout). |
| **Instructions** | `instructions_filename` | File the installer injects agent instructions into (`AGENTS.md`, `GEMINI.md`). `None` for claude (uses `CLAUDE-VAULT.md`) and pi. |

## Built-in runtimes

Registered at module import by `_register_builtin_adapters()`:

| Runtime | Hooks | Connect path | Notes |
|---|---|---|---|
| `claude` | `settings.json` | `install()`/`uninstall()` (native hooks) | Keeps its own `merge_hooks` flow (AI-mode timeout raise, update-existing-options, SEC-105 `.bak` snapshot); reads `event_scripts` from the adapter. Since ARC-002, `session_stop_hook.py` is a shim over `run_session_end` with this adapter (`read_transcript_tail` byte-bounded reader, `always_log_daily=true`). |
| `codex` | `~/.codex/hooks.json` | `install()`/`uninstall()` | Generic `_merge_runtime_hooks` / `remove_runtime_hooks`. Timeout in **seconds**. |
| `gemini` | `~/.gemini/settings.json` | `install()`/`uninstall()` | Generic core. Requires per-event `name`; timeout in **ms**. |
| `pi` | none | `connect pi` runs `scripts/install-pi-extension` | Extension-only: ships a TypeScript extension that shells out to claude's hook scripts at runtime (preferring `uv run --no-project`). |
| `omp` | none | `connect omp` runs `scripts/install-pi-extension --extension-dir <omp-home>/agent/extensions` | Extension-only, same source as pi. omp resolves its agent dir from `$PI_CONFIG_DIR` (default `~/.omp`); `--omp-home` overrides. omp's extension loader resolves the extension's `@mariozechner/*` imports and emits every event it uses; subagent capture is a no-op under omp (no `subagent:result` messages). |

## Adding a runtime

**Hooks-only runtime** (the common case): add one `AgentAdapter` to `_register_builtin_adapters()`
(or drop a file in the [external dir](#external-adapters)). Because `event_scripts`, the entry shape,
and the config filename are all descriptor fields, the generic `_merge_runtime_hooks` /
`remove_runtime_hooks` immediately serve it — no new installer functions, no new scripts. It also
appears automatically in `install.py connect`/`disconnect` and `known_runtimes()`.

```python
register(
    AgentAdapter(
        name="acme",
        display_name="Acme",
        runtime_env_value="acme",
        hook_event_name_start="AcmeSessionStart",
        hook_event_name_end="AcmeSessionEnd",
        hooks_config_filename="hooks.json",
        event_scripts={"SessionStart": "acme_session_start_hook.py"},
        entry_matcher="",
        entry_timeout=60,
        timeout_unit="s",
        instructions_filename="AGENTS.md",
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
2. **Permission check.** Each file is refused if it is group- or world-writable.
3. **Load-time logging.** Every externally-loaded adapter (and every refusal/failure) is logged to
   stderr by path.

Never raises — a broken external adapter cannot break the registry or the hooks that read it.

## Timeout units

The `timeout_unit` field exists because the runtimes disagree: Codex's `hooks.json` `timeout` is in
**seconds** (`Duration::from_secs` in codex-rs), while Claude's `settings.json` and Gemini's
`settings.json` use **milliseconds**. Stating the unit in the descriptor makes it explicit and
checkable instead of an undocumented literal (audit item ARC-048a found Gemini's `10000` with no
comment saying which scale applied).

## Stdlib-only rule

`agent_adapter.py` and every hook shim import nothing outside the Python standard library (plus the
stdlib-only `vault_common`). This is the project's hardest constraint and it is enforced by
`tests/test_stdlib_only.py`, which imports every `core/*` module and hook in a fresh interpreter with
`rich`/`fastembed`/`sqlite_vec`/`anyio`/`yaml`/`numpy`/`PIL` poisoned in `sys.modules`. Any adapter
descriptor that pulls a third-party import — even transitively — fails the gate.

## Architecture notes

- **One descriptor, two consumers.** The hook shims (`codex_session_start_hook.py`, …) call
  `run_session_start`/`run_session_end` with their adapter; the installer's `_merge_runtime_hooks` /
  `remove_runtime_hooks` read the same adapter's installer-side fields.
- **Installer → scripts dependency.** The installer imports `agent_adapter` from
  `skills/parsidion/scripts/`. `installer/__init__.py` puts that directory on `sys.path` at package
  import (established precedent — `installer/paths.py` already imports `vault_path` the same way).
  This is safe because `vault_common` is stdlib-only at import time.
- **Generic core.** `_merge_runtime_hooks(adapter, …)` and `remove_runtime_hooks(adapter, …)` in
  `installer/hooks.py` are the single read-modify-write path for codex/gemini (and any future
  hooks-based runtime). Claude retains its own `merge_hooks` for its AI-mode/options/`.bak` flow but
  reads `event_scripts` and builds commands through the shared helpers.

## Related documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — system architecture and hook lifecycle.
- [SECURITY.md](../SECURITY.md) — the hook-execution and installer security surface (runtimes + scope table).
- [CLAUDE.md](../CLAUDE.md) — the runtime-adapter hook components and the stdlib-only rule.
