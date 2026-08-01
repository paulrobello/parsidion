# ENH-014 — Typed config schema via stdlib dataclasses

## Goal
Replace the untyped-dict config returned by `core/vault_config.py` with validated dataclass schemas per section, so a typo'd config key is caught at startup instead of silently falling back to the default — without violating the stdlib-only constraint (no pydantic/ruamel).

## Current-state context
- `skills/parsidion/scripts/core/vault_config.py:load_config()` parses `config.yaml` with a hand-rolled stdlib YAML parser (one level of nesting, inline comments, scalars), `lru_cache`'d, returns a deep-copied dict. Distinguishes "key absent" from "explicit null" (good).
- Callers read values via `get_config(section, key, default)` — there is no schema, so `session_start_hook.ai_model` vs a typo'd `ai_model` both "work" (the typo returns the default). CLAUDE.md documents ~14 config sections with many keys each.
- The flat shim `vault_config.py` re-exports the API; `vault_hooks.py`/the hooks read config throughout.
- Constraint: `core/vault_config.py` is stdlib-only (covered by `tests/test_stdlib_only.py`). Dataclasses are stdlib — allowed. `pydantic`/`ruamel.yaml`/`tomllib-with-validation` frameworks are NOT.

## Step-by-step implementation
1. **Define dataclasses** per documented section in a new `core/vault_schema.py` (stdlib only): `SessionStartHookConfig`, `SessionStopHookConfig`, `SubagentStopHookConfig`, `PreCompactHookConfig`, `SummarizerConfig`, `AIConfig`, `AIModelsConfig`, `CodexCLIConfig`, `EmbeddingsConfig`, `ParMemConfig`, `SearchConfig`, `AnthropicEnvConfig`, `GitConfig`, `EventLogConfig`, `AdaptiveContextConfig`, `VaultConfig`, `DefaultsConfig`, plus a top-level `VaultAppConfig` aggregating them. Each field has a default and a type.
2. **Add `validate()`**: a method (or `@classmethod from_dict`) that maps the parsed dict onto the dataclasses, coercing types (str→int for timeouts, bool parsing) and collecting **unknown-key warnings**. On a type-coercion failure, raise a clear error naming the section/key. On an unknown key, emit a warning (do not fail — forward-compat for new keys).
3. **Wire into `load_config()`**: after parsing the YAML dict, run it through `VaultAppConfig.from_dict()`; cache and return the dataclass instance (deep-copied). Keep `get_config(section, key, default)` working as a compatibility shim over the dataclass (so the ~many callers don't all change at once), but add typed accessors (`config.session_start_hook.ai_model`).
4. **Migrate callers incrementally**: leave `get_config()` callers as-is initially; migrate the highest-value ones (the hooks) to typed access in follow-up commits.
5. **Dump a generated config reference**: optional — derive a `docs/CONFIG.md` table from the dataclass fields (closes part of DOC-006's "README omits sections" by making the canonical list generated).

## Files to touch
- `skills/parsidion/scripts/core/vault_schema.py` (new — the dataclasses + `from_dict`)
- `skills/parsidion/scripts/core/vault_config.py` (`load_config` returns a dataclass; `get_config` shim)
- `skills/parsidion/scripts/vault_config.py` (flat shim re-exports the new types)
- `tests/test_vault_config*.py` (add: unknown-key warning, type-coercion error, default-applied, explicit-null handling)
- `tests/test_stdlib_only.py` (confirm `core/vault_schema.py` is in the stdlib-only scope list and passes the poison gate)

## Verification
- `make checkall` (incl. the stdlib-only poison gate — `vault_schema.py` must import cleanly with `dataclasses` only).
- New tests: a config with `summarizer: { max_parallel: "not-a-number" }` raises a clear error; `session_start_hook: { bogus_key: 1 }` emits an unknown-key warning; a missing key still returns the default via `get_config()`.
- Back-compat: run a session-start hook with the existing `config.yaml` and confirm identical behavior (the `get_config()` shim preserves it).

## Rollback
- `load_config` returning a dataclass vs a dict is the one integration seam. Keep `get_config()` as a dict-style shim over the dataclass for the first release, so reverting means restoring the dict return — callers using `get_config()` are unaffected. The dataclasses in `vault_schema.py` are additive and can be dropped independently.
