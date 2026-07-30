# ENH-006 — `AgentAdapter` registry with a documented third-party extension point

> **Impact**: medium · **Effort**: medium · **Status**: not started
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`
> **Sequencing: land audit item QA-008 / ARC-020 first.** That work collapses the five copy-pasted hook
> scripts into one parameterized module. This enhancement promotes the result into a public, documented
> contract and brings the pi extension under it. Doing this first means doing the collapse twice.

## Goal

Make "add a new coding-agent runtime" a **data-only** change against a documented contract, rather than
the current copy-2-to-3-scripts-plus-4-installer-functions ritual. Parsidion's stated identity is
"agent-agnostic"; this is what makes that structurally true rather than aspirational.

## Current state

The audit found three parallel mechanisms for what is conceptually one thing:

**1. The copy-paste family.** `codex_session_start_hook.py` (77 LOC) and `gemini_session_start_hook.py`
(78 LOC) differ only in docstrings and one literal — `os.environ["PARSIDION_RUNTIME"] = "codex"` vs
`"gemini"`, both on line 48. `codex_stop_hook.py` (107) and `gemini_session_end_hook.py` (107) differ
only in two function references, with the entire 60-line `main()` duplicated verbatim. Plus
`codex_subagent_stop_hook.py` (100). That is 469 lines whose real variation is three symbols:

- `is_codex_transcript_path` / `is_gemini_transcript_path`
- `parse_codex_transcript_lines` / `parse_gemini_transcript_lines`
- `PARSIDION_RUNTIME = "codex"` / `"gemini"`

**2. The installer family.** `installer/hooks.py:57-78,227-331` and `installer/paths.py:42-87` carry
per-agent `_managed_*_hook_command`, `merge_*_hooks`, `remove_*_hooks`, and `_wants_*_runtime`, plus
matching branches in `install()` and `uninstall()`.

**3. The pi extension.** `extensions/pi/parsidion/` (914 LOC of TypeScript) is installed by a standalone
bash script `scripts/install-pi-extension`, is unknown to `install.py connect` (which accepts only
`claude|codex|gemini`), and invokes scripts via bare `python3`/`python` (`parsidion.ts:370,399`) rather
than the `uv run --no-project` every other caller uses. It also has a `parsidion-status.test.ts` that no
Makefile target or CI job executes.

The concrete cost is not just duplication. Because the Codex and Gemini stop hooks delegate to nothing
and reimplement the queueing pipeline, they call `write_hook_event` and `git_commit_vault` **zero**
times each — versus twice each in `session_stop_hook.py`. So `vault-stats --hooks` is blind to every
Codex and Gemini session, and a Codex-only user's vault silently accumulates uncommitted daily-note
changes. That is a capability gap produced directly by the duplication, and it is the strongest
argument that this is worth doing properly rather than just deduplicating.

## Design

One declarative descriptor, one registry, three consumers (hook entrypoints, installer, docs).

```python
@dataclass(frozen=True)
class AgentAdapter:
    """Everything that varies between coding-agent runtimes.

    Adding a runtime means adding one instance of this and registering it.
    No new scripts, no new installer functions.
    """
    name: str                              # "claude" | "codex" | "gemini" | "pi" | third-party
    display_name: str
    runtime_env_value: str                 # PARSIDION_RUNTIME
    instructions_file: Path                # ~/.codex/AGENTS.md, ~/.gemini/GEMINI.md, ...
    hooks_config_path: Path                # where hook registrations live
    hook_entries: Callable[[Path], dict]   # builds the runtime's hook-registration structure
    timeout_unit: Literal["ms", "s"]       # Claude uses ms; Codex is documented in seconds
    transcript_validator: Callable[[Path], bool]
    transcript_parser: Callable[[Iterable[str]], list[dict]]
    transcript_tail_lines: int = 500
```

`hook_entries` stays a callable because the registration *shape* differs per runtime (Claude's
`settings.json` hooks array, Codex's TOML, Gemini's own file). Everything else is data.

Keep this **stdlib-only** — it lives beside the hook scripts, which are bound by the project's hardest
constraint.

## Implementation

### Step 1 — Land QA-008 first, then extract the descriptor

Confirm QA-008's parameterized module exists. Its runtime descriptor is the seed of `AgentAdapter`;
promote it from an internal tuple/dict into the frozen dataclass above, in a new
`skills/parsidion/scripts/agent_adapter.py`.

### Step 2 — Registry with discovery

```python
_REGISTRY: dict[str, AgentAdapter] = {}

def register(adapter: AgentAdapter) -> None: ...
def get(name: str) -> AgentAdapter: ...
def known_runtimes() -> list[str]: ...
```

Register the four built-ins (`claude`, `codex`, `gemini`, `pi`) at module import.

For third-party adapters, support a directory drop-in — `~/.config/parsidion/adapters/*.py`, each
defining a module-level `ADAPTER: AgentAdapter` — loaded via `importlib`. Two constraints, both
non-negotiable:

- Loading arbitrary Python from a config directory is code execution. It is *user-owned* code so this
  is acceptable, but it must be **opt-in** behind an explicit `adapters.load_external: true` config key
  (default `false`), and the loader must refuse files that are group- or world-writable. This repo has
  an existing precedent for exactly this reasoning in how `codex_cli.command` is treated
  (audit item SEC-117) — follow it.
- Log every externally-loaded adapter by path at load time. Silent extension is how supply-chain
  surprises happen.

### Step 3 — Generic hook entrypoints

Two functions replacing the five scripts:

```python
def run_session_start(adapter: AgentAdapter) -> None: ...
def run_session_end(adapter: AgentAdapter) -> None: ...
```

Fold `write_hook_event` and `git_commit_vault` into **both**, which closes the observability and
auto-commit gap for Codex and Gemini as a structural consequence rather than as five separate fixes.

The five existing scripts become three-line shims kept for backward compatibility with already-installed
`settings.json` entries:

```python
#!/usr/bin/env python3
"""Codex SessionStart hook — thin shim over the shared adapter entrypoint."""
from agent_adapter import get, run_session_start

run_session_start(get("codex"))
```

Do not delete them. Existing installations reference these exact paths.

### Step 4 — Collapse the installer

Replace `merge_codex_hooks`/`merge_gemini_hooks` and `remove_codex_hooks`/`remove_gemini_hooks` with
two generic functions driven by the registry. Replace the per-agent `_wants_*_runtime` predicates with a
single lookup over `known_runtimes()`. Change `install.py connect`'s argparse `choices` from the
hardcoded `claude|codex|gemini` to `known_runtimes()`.

Coordinate carefully with audit items ARC-003 (guards the unconditional teardown in `uninstall()`),
ARC-017 (Phase 5 restructure of `install()`/`uninstall()`), and ARC-018 (atomic settings writes). If
ARC-017 has landed, the registry becomes the natural source of the step list rather than a separate
mechanism — prefer that.

### Step 5 — Bring pi into the registry

- Add a `pi` adapter describing `extensions/pi/parsidion/`.
- Make `install.py connect pi` work, delegating to (or replacing) `scripts/install-pi-extension`.
- Fix `parsidion.ts:370,399` to invoke `uv run --no-project` like every other caller, rather than bare
  `python3`/`python`. That inconsistency is a real bug: it picks up whatever `python3` is on `PATH`,
  which may lack the `search` extra.
- Add the pi test file to CI (audit item ARC-007 creates the `extensions` job; if it landed, just
  confirm `parsidion-status.test.ts` runs).

### Step 6 — Document the contract

New `docs/AGENT-ADAPTERS.md`:

- The `AgentAdapter` field reference, with the meaning and constraints of each.
- A complete worked example adding a hypothetical runtime end to end.
- The stdlib-only rule for anything in the hook path, and why.
- The `timeout_unit` distinction — Claude's ms convention versus Codex's documented seconds
  (audit item ARC-048a found the Gemini hook using `10000` with no comment saying which applies;
  the adapter's `timeout_unit` field is what makes that explicit and checkable).
- The external-adapter opt-in and its security implications.

Link it from `README.md`, `CLAUDE.md`, and `docs/ARCHITECTURE.md`.

### Step 7 — Tests

1. **Parameterized over every registered adapter** — one test body, N runtimes. Replaces what would
   otherwise be five copies.
2. **Observability parity** — for every adapter, a session-end run writes exactly one `hook_events.log`
   entry and invokes `git_commit_vault`. This is the regression guard for the gap that motivated the
   work; assert it per-adapter so a new runtime cannot reintroduce it.
3. **Shim equivalence** — each legacy script produces byte-identical `pending_summaries.jsonl` output to
   its adapter-driven equivalent, using recorded fixtures.
4. **Registry completeness** — `known_runtimes()` matches `install.py connect`'s accepted choices.
   Prevents the current class of drift where the CLI and the implementation disagree.
5. **External loading is off by default**; a permissive-mode adapter file is refused.
6. **stdlib-only enforcement** — import `agent_adapter` and every hook shim with `sys.modules` poisoned
   against `rich`, `fastembed`, `sqlite_vec`. (If ARC-004 landed, reuse its enforcement test.)

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/agent_adapter.py` | new — dataclass, registry, generic entrypoints |
| `skills/parsidion/scripts/{codex,gemini}_*.py` (5 files) | reduce to shims |
| `installer/hooks.py`, `installer/paths.py` | collapse per-agent functions to generic ones |
| `install.py` | `connect`/`disconnect` choices from the registry |
| `extensions/pi/parsidion/parsidion.ts` | `uv run --no-project` instead of bare `python3` |
| `scripts/install-pi-extension` | delegate to, or be replaced by, `install.py connect pi` |
| `skills/parsidion/scripts/vault_config.py`, `templates/config.yaml` | `adapters.load_external` |
| `docs/AGENT-ADAPTERS.md` | new |
| `tests/test_connect.py`, new `tests/test_agent_adapter.py` | the six tests above |
| `README.md`, `CLAUDE.md`, `docs/ARCHITECTURE.md`, `SECURITY.md` | link the new doc; add pi and any new runtime to SECURITY.md's scope table (see audit item DOC-011) |

## Verification

```bash
uv run ruff format --check . && uv run ruff check . && uv run pyright .
uv run pytest tests/ -v
make checkall

# Contract proof: adding a runtime must be data-only.
# Write a throwaway adapter for a fake "acme" runtime in a temp adapters dir,
# enable adapters.load_external, and confirm:
uv run install.py connect acme --dry-run     # appears in the plan, no new scripts written
uv run python -c "
import sys; sys.path.insert(0,'skills/parsidion/scripts')
import agent_adapter; print(sorted(agent_adapter.known_runtimes()))
"
# Then remove the temp adapter and confirm it disappears from the list.

# Observability regression check — the gap that motivated this work
uv run pytest tests/test_agent_adapter.py -k observability -v
```

## Rollback

The five legacy scripts remain as shims, so already-installed `settings.json` entries keep working
regardless. Reverting means restoring their bodies from git — no user-side migration, because the hook
*paths* never change. External adapter loading is default-off, so that surface does not exist unless
explicitly enabled. The installer collapse is the largest piece; keep it as its own commit so it can be
reverted independently of the hook-side work.

## Risks

- **External adapters are code execution.** Handled by default-off, a permissive-mode file check, and
  load-time logging. Do not weaken any of the three; if in doubt, ship Steps 1–6 without external
  loading and add it later.
- **Installer churn colliding with Phase 5 audit work.** ARC-017 restructures the same functions.
  Sequence explicitly rather than merging both at once — this plan should follow ARC-017 if that is
  scheduled, and the registry should feed its step list.
- **Over-abstraction.** Four runtimes is enough to justify a registry and not enough to justify a plugin
  framework. Resist adding lifecycle hooks, priorities, or capability negotiation that no current
  adapter needs. If a field is used by exactly one adapter, it does not belong in the dataclass.
