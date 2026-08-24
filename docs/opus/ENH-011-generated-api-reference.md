# ENH-011 — Generated API reference under `docs/api/`

> **Status**: shipped 2026-08-01

## Goal
Generate an always-current API reference from the code's existing docstrings/JSDoc so the public surface (Python `core/*`, `vault_*` CLIs, and the visualizer TS lib) is discoverable without reading source — closing the gap the documentation audit noted ("no generated API reference or `docs/api/` directory").

## Current-state context
- Python public symbols have Google-style docstrings (Args/Returns) — verified by the audit on `resolve_vault`, `_connectable_runtimes`, `vault_new.py`, etc.
- The visualizer `lib/*` modules have JSDoc + co-located `*.test.ts` files; types live in `lib/graph.ts`, `lib/vaultResolver.ts`.
- `docs/MCP.md` documents only the 8 MCP tools; the Python module surface and TS lib have no generated reference.
- Constraint: doc *generation* tooling (pdoc, TypeDoc) is a dev dependency, not a runtime one — it does not violate the stdlib-only hook/CLI constraint (that constraint applies to `skills/parsidion/scripts/` runtime code, not to dev tooling in `pyproject.toml` `[dev]` / the visualizer).

## Step-by-step implementation
1. **Python reference (pdoc)**: add `pdoc` to `[project.optional-dependencies] dev` (or a new `docs` extra). Configure it to document `skills/parsidion/scripts/core/`, the `vault_*` CLI entrypoints, and `installer/`. Output to `docs/api/python/`.
2. **TS reference (TypeDoc)**: add `typedoc` to `visualizer/package.json` devDeps; configure to document `visualizer/lib/`. Output to `docs/api/visualizer/`.
3. **Makefile targets**: add `make docs-api` (runs both generators) and `make docs-api-check` (CI gate: regenerate to temp, diff against committed — mirrors `make parity-fixtures-check`). Add `docs-api-check` to `make checkall` as a non-blocking advisory or a full gate (decide based on churn).
4. **.gitignore vs. commit**: decide whether `docs/api/` is committed (discoverable on GitHub, but diff-noisy) or generated in CI. Recommended: commit a snapshot + gate on drift, so the repo shows current docs but CI catches staleness.
5. **Link from README/docs**: add a pointer in `docs/README.md` and the root README's "Components"/"Related Docs" to `docs/api/`.

## Files to touch
- `pyproject.toml` (pdoc in a `docs` extra)
- `visualizer/package.json` (typedoc devDep)
- `Makefile` (`docs-api`, `docs-api-check` targets)
- `docs/api/` (generated output — committed snapshot)
- `docs/README.md`, `README.md` (pointers)
- `.pre-commit-config.yaml` (optional: skip the generated `docs/api/` from formatting)

## Verification
- `make docs-api` runs clean and regenerates `docs/api/`.
- `make docs-api-check` exits 0 on a fresh generation (no drift).
- `make checkall` still green (the generators must not interfere with the stdlib-only gate — they are dev-only).
- Spot-check: `docs/api/python/core/vault_path.html` (or markdown) documents `resolve_vault` with its docstring.

## Rollback
- Remove the `docs-api` Makefile targets, the dev deps, and `docs/api/`. The generators are dev-only and additive — removing them affects nothing at runtime. If committed `docs/api/` is diff-noisy, switch to CI-only generation.
