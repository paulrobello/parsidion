# ENH-017 — Generate the config reference from `vault_schema.py` and gate it in CI

> Status: done (2026-08-24; kanban 01a0319f53667b309e361ca46733a4e0)
> Impact: high · Effort: M · Related: DOC-004, DOC-008 (the one-off manual sync this makes permanent), ARC-007 (schema defaults)

## Goal

Stop the config table in `CLAUDE.md`, the config block in `docs/ARCHITECTURE.md`, and
`skills/parsidion/templates/config.yaml` from drifting apart from the keys the code reads, by
generating all three from `core/vault_schema.py` and failing CI when the committed copies differ.

## Current state

- DOC-004 and DOC-008 found eight keys missing or wrong across the three hand-maintained
  documents, plus a key (`decay_days`) documented but unread and a template that flips three codex
  defaults.
- `core/vault_schema.py:302` `VaultAppConfig` already enumerates every section and key with types;
  after ARC-007 it also carries defaults. It has no docstrings per field.
- ENH-011 established the pattern: `make docs-api` generates, `make docs-api-check` regenerates to
  a temp dir and diffs (`Makefile:66-79`).

## Implementation

1. **Field metadata.** In `vault_schema.py`, attach a one-line description and the reader module
   to each field via `dataclasses.field(metadata={"doc": "...", "read_by": "session_start_hook.py"})`.
   Keep the module stdlib-only.
2. **Generator.** New `scripts/gen_config_docs.py` (repo `scripts/`, not the skill; stdlib only)
   that imports `core.vault_schema` and emits:
   - `docs/generated/config-table.md`: the markdown table currently at `CLAUDE.md:222-250`
     (section, keys, used-by);
   - `docs/generated/config-reference.md`: the per-key block currently at
     `docs/ARCHITECTURE.md:1038-1129` (key, type, default, description);
   - `skills/parsidion/templates/config.yaml`: the template, with every key commented with its
     description and default (preserve the existing header prose from a `templates/config.header.yaml`).
   Support `--check` (write to a temp dir, diff, exit 1 on drift) mirroring `gen_parity_fixtures.py`.
3. **Include, don't inline.** Replace the hand-written table in `CLAUDE.md` and the block in
   `docs/ARCHITECTURE.md` with a short pointer plus the generated file content pasted by the
   generator between `<!-- config-table:start -->` / `<!-- config-table:end -->` markers, so the
   generator rewrites only that region.
4. **Makefile + CI.** `make config-docs` and `make config-docs-check`; add the check to
   `make checkall` and a CI job next to `parity-fixtures-check`.
5. **Schema-vs-code test.** `tests/test_config_schema_coverage.py`: AST-scan every
   `get_config("section", "key", ...)` call under `skills/parsidion/scripts` and assert each
   `(section, key)` exists in the schema, and every schema key has at least one reader or is
   marked `metadata={"reserved": True}`. This is what catches a future `decay_days`.

## Files to touch

- `skills/parsidion/scripts/core/vault_schema.py`
- new `scripts/gen_config_docs.py`, `tests/test_config_schema_coverage.py`, `tests/test_gen_config_docs.py`
- `CLAUDE.md`, `docs/ARCHITECTURE.md`, `skills/parsidion/templates/config.yaml`, new `templates/config.header.yaml`
- `Makefile`, `.github/workflows/ci.yml`, `docs/README.md`

## Verify

- `make config-docs && make config-docs-check` exits 0; a deliberate edit to a schema description
  makes `config-docs-check` exit 1.
- `uv run pytest tests/test_config_schema_coverage.py tests/test_gen_config_docs.py -q` passes and
  fails when a fake `get_config("nope", "x", 1)` call is added to a script.
- `diff <(uv run python scripts/gen_config_docs.py --print template) skills/parsidion/templates/config.yaml` is empty.
- `make checkall` exit 0.

## Rollback

Delete the generator and the Makefile/CI targets; the generated regions remain valid static
markdown. Keep the schema-vs-code test even on rollback: it is standalone.
