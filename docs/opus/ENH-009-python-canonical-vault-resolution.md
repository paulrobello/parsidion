# ENH-009 — Make vault resolution Python-canonical

> **Status**: shipped 2026-08-01

> Reframed 2026-08-01. The original framing ("serve vault resolution through
> the parsidion-mcp server") ran against Parsidion's stated architecture: the
> Python CLI/skill layer is the access layer of record, and the MCP server is
> explicitly **optional** — nothing on the critical path may depend on it. The
> visualizer does not use MCP today, so routing resolution through MCP would
> *add* a dependency where none exists. This revision eliminates the duplicate
> resolver the right way: the visualizer delegates to the same Python resolver
> the hooks use, over the subprocess path it already speaks.

## Goal

Collapse the two independent vault-resolution implementations (Python
`resolve_vault` + TypeScript `resolveVault`) into one. Python becomes the single
source of truth; the visualizer's TypeScript resolver becomes a thin subprocess
caller. This removes the drift class flagged by QA-012 / ARC-007 / SEC-P001 at
its root — there is no second implementation to drift — **without** introducing
any MCP dependency.

## Why not MCP

- `parsidion-mcp` is documented as an **optional** server for MCP clients
  (Claude Desktop et al.). It is not on the hook, CLI, or visualizer path.
- The visualizer already shells out to Python for the heavy lifting
  (`build_graph.py`, `vault_search.py`, `vault-stats`) via the shared
  `runScript` / `findParsidionScript` helpers. Resolution is a smaller instance
  of the same pattern — not a new architectural hop.
- The MCP server itself imports `vault_common` / the parsidion core to resolve
  vaults. Putting the visualizer behind MCP would add a server dependency to
  reach logic that already runs in a plain `uv run` subprocess.

## Current-state context

- `skills/parsidion/scripts/core/vault_path.py:380` `resolve_vault()` is the
  authoritative resolver. Precedence: explicit → `cwd/.claude/vault` →
  `CLAUDE_VAULT` env → default (`~/ParsidionVault`, legacy `~/ClaudeVault`).
  Allowlist-hardened (SEC-P001), cached (`lru_cache`).
- `visualizer/lib/vaultResolver.ts` `resolveVault()` is a **narrower**
  reimplementation: named vaults from `vaults.yaml` + default + a `VAULT_ROOT`
  env override. It deliberately omits the `cwd/.claude/vault` and `CLAUDE_VAULT`
  channels (ARC-007) because the visualizer is a long-lived server with no
  "current project" and no inherited runtime env.
- The two are pinned — but only on their shared subset — by
  `tests/fixtures/parity/vault-resolution.json` +
  `tests/test_vault_resolver_parity.py` +
  `visualizer/lib/vaultResolver.parity.test.ts`. The implementations genuinely
  differ; the fixture scopes the differences with `applies_to`.
- `guardPath()` / `validateVaultPath()` / the `realpath*` helpers in
  `vaultResolver.ts` are **not** vault resolution — they are HTTP-input
  containment checks (does this note path the client sent fall inside the vault
  root?). Python never sees request paths, so these stay in TypeScript.

## Design

A new **server-context** resolver formalizes the narrow contract the visualizer
needs, as a public Python function — not a second algorithm:

- `resolve_vault_server(reference: str | None = None) -> Path` in
  `core/vault_path.py`. Named vault (from `vaults.yaml`) if `reference` matches,
  else the default vault. Honors the `VAULT_ROOT` env override for the default
  (preserving today's TS `getDefaultVault()` behavior). Does **not** consult
  `cwd/.claude/vault` or `CLAUDE_VAULT`. Reuses `list_named_vaults()`,
  `_resolve_vault_reference()`, `_validate_vault_path()`, `default_vault_root()`
  — it is policy (which channels apply to a server), not a re-derived algorithm.

A stdlib-only CLI exposes it to any non-Python caller:

- `skills/parsidion/scripts/vault_resolve.py` — `vault_resolve.py [NAME]`
  prints the resolved path; `--list` prints
  `{"default": "...", "named": [{"name","path"},...]}` as JSON. Exits non-zero
  with a stderr message on `VaultConfigError`, so the caller can map it back to
  the typed error. Stdlib-only (hook/script constraint).

The TypeScript side delegates instead of re-implementing:

- `visualizer/lib/vaultResolver.ts` keeps its **exact public surface**
  (`resolveVault`, `getDefaultVault`, `listNamedVaults`, `VaultConfigError`,
  `guardPath`, `validateVaultPath`) and filename, so the 15 importing route
  files change **not at all**. Internally `resolveVault` / `getDefaultVault` /
  `listNamedVaults` call `vault_resolve.py` through the existing
  `runScript` + `findParsidionScript` path (`uv run --no-project`). A lazy
  in-process cache keyed by name (invalidated on `vaults.yaml` mtime) avoids a
  subprocess spawn on every request after the first.
- `guardPath` and the `realpath*` helpers are untouched.

## Step-by-step implementation

1. **Python core** — add `resolve_vault_server()` (and a `_server_default_vault()`
   helper that folds in the `VAULT_ROOT` override) to `core/vault_path.py`, with
   a docstring noting it is the canonical server resolver the visualizer
   delegates to. Add a focused unit test alongside the existing parity test.
   → verify: `uv run pytest tests/test_vault_resolver_parity.py` (and the new
   test).
2. **Python CLI** — add `scripts/vault_resolve.py` (stdlib-only) wrapping
   `resolve_vault_server` + `list_named_vaults`. Verify the stdlib-only gate
   still passes (`tests/test_stdlib_only.py`).
   → verify: `uv run --no-project skills/parsidion/scripts/vault_resolve.py`
   resolves the default; `--list` emits JSON; an unknown name exits non-zero.
3. **TypeScript delegation** — rewrite the internals of `resolveVault` /
   `getDefaultVault` / `listNamedVaults` in `vaultResolver.ts` to call the
   script via `runScript` + `findParsidionScript`, behind the cache. Keep
   `guardPath`, `validateVaultPath`, realpath helpers, the `VaultConfigError`
   class, and all exports identical.
   → verify: `make visualizer-check` (tsc + lint + bun test).
4. **Parity rework** — there is now one resolution implementation, so the
   "two independent implementations" contract is gone. Reduce
   `vaultResolver.test.ts`'s resolution cases to delegation smoke tests (keep
   the `guardPath` path-traversal suite intact); rework
   `vaultResolver.parity.test.ts` and `tests/test_vault_resolver_parity.py` so
   the fixture pins the single Python resolver's behavior and the TS side
   asserts it reaches the same answer through the subprocess.
   → verify: `make parity-fixtures-check`.
5. **Docs / comments** — update the `resolve_vault()` docstring and the
   `vaultResolver.ts` header (the "long-term plan: serve via parsidion-mcp"
   notes become "resolved: Python-canonical via `vault_resolve.py`"); touch the
   ENH-005 / ARC-007 note in `CLAUDE.md`. `docs/MCP.md` needs **no** change — no
   MCP tool is added.

## Files to touch

- `skills/parsidion/scripts/core/vault_path.py` — add `resolve_vault_server`.
- `skills/parsidion/scripts/vault_resolve.py` — new stdlib CLI.
- `tests/test_vault_resolver_parity.py` — cover `resolve_vault_server`.
- `visualizer/lib/vaultResolver.ts` — delegate resolution to Python.
- `visualizer/lib/vaultResolver.test.ts`, `visualizer/lib/vaultResolver.parity.test.ts` — rework.
- `tests/fixtures/parity/vault-resolution.json` — reflect single-sourcing (if the contract shape changes).
- `CLAUDE.md`, inline docstrings/comments — update the long-term-plan notes.
- an earlier draft plan for MCP-side vault resolution (filename `ENH-009-mcp-vault-resolution`) — deleted from `docs/opus/`; superseded by this file.

## Verification

- `make checkall` (root gate incl. `make checkall-mcp` + `make visualizer-check`).
- `make parity-fixtures-check` passes.
- `make install` then start the visualizer (`make visualizer`); confirm a
  named vault and the default both resolve identically to the hooks
  (`uv run --no-project skills/parsidion/scripts/vault_resolve.py myvault` vs.
  the `/api/vaults` listing).
- `find_broken_doc_links` clean (no dangling links from the doc rename).

## Rollback

The TS allowlist and the old plan doc are preserved in git history. Revert the
`vaultResolver.ts` delegation and drop `resolve_vault_server` / `vault_resolve.py`
to restore the duplicated two-resolver state. The parity fixture is regenerated
by `make parity-fixtures`, so reverting it is trivial.

## Out of scope

- Adding the `cwd/.claude/vault` or `CLAUDE_VAULT` channels to the server. A
  long-lived server has no project context; the narrower contract is correct.
- Any change to `parsidion-mcp`. It already resolves via the Python core; it is
  untouched and remains optional.
