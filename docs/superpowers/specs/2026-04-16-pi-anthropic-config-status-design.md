# pi Anthropic Config Status Design

**Date:** 2026-04-16
**Project:** parsidion
**Status:** Proposed

## Summary

Add pi-facing visibility for Parsidion's Anthropic-compatible runtime configuration without duplicating Python runtime behavior.

The existing Python hook scripts are now the source of truth for `anthropic_env` values defined in `~/ClaudeVault/config.yaml`. The pi extension should expose clear status about where those values are coming from, but should not reimplement or override Python config resolution. This keeps runtime behavior centralized while making `/parsidion-vault` materially more useful for debugging GLM/Z.AI-compatible setups.

## Goals

1. Show Anthropic-compatible config status in the pi extension's `/parsidion-vault` output.
2. Make it obvious whether each setting comes from:
   - the live process environment
   - vault config (`~/ClaudeVault/config.yaml` → `anthropic_env`)
   - neither (unset)
3. Avoid duplicating Python runtime logic in TypeScript.
4. Update all relevant docs so the pi extension, vault config, and runtime precedence are documented consistently.

## Non-Goals

1. Do not move runtime authority away from Python hook scripts.
2. Do not make the pi extension responsible for applying or exporting Anthropic env vars to child processes.
3. Do not introduce a second config format for GLM/Anthropic settings.
4. Do not support the deprecated/ignored `...MODEL1` variable names.

## Background

Parsidion hook/runtime behavior now supports Anthropic-compatible configuration values from vault config under:

```yaml
anthropic_env:
  ANTHROPIC_AUTH_TOKEN: ...
  ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic
  API_TIMEOUT_MS: 3000000
  ANTHROPIC_DEFAULT_HAIKU_MODEL: GLM-5-TURBO
  ANTHROPIC_DEFAULT_SONNET_MODEL: GLM-5.1
  ANTHROPIC_DEFAULT_OPUS_MODEL: GLM-5.1
```

with precedence:

**real environment variable > vault config > default behavior**

The pi extension currently launches Parsidion Python scripts, but its status output does not reveal whether this Anthropic/GLM configuration is set or which source is winning.

## Chosen Approach

### Approach 1: Docs only

Update documentation but leave `/parsidion-vault` unchanged.

**Pros**
- lowest implementation cost
- no runtime risk

**Cons**
- poor observability
- users still cannot easily tell whether env or vault config is active

### Approach 2: Docs + `/parsidion-vault` status output (**Chosen**)

Keep Python as runtime authority. Extend the pi extension status command to inspect available config sources and render a concise, masked summary.

**Pros**
- good debugging visibility
- low logic duplication
- minimal runtime risk
- aligns with current architecture

**Cons**
- extension contains some read-only config inspection logic
- status can report state but does not enforce it

### Approach 3: Full extension-side env orchestration

Have the extension resolve config and explicitly inject env vars for all child Python processes.

**Pros**
- strongest extension-side control

**Cons**
- duplicates Python resolution logic
- higher drift risk
- unnecessary for current requirements

## Architecture

### Runtime authority

Python remains authoritative for runtime configuration:
- `skills/parsidion/scripts/vault_hooks.py`
- `skills/parsidion/scripts/vault_common.py`
- Python scripts that call `apply_configured_env_defaults()` or `env_without_claudecode()`

### pi status authority

The pi extension will perform a **read-only inspection** of the following sources for status rendering:

1. `process.env`
2. `~/ClaudeVault/config.yaml` (resolved via `CLAUDE_VAULT` if present, otherwise default vault)

It will not use those inspected values to alter runtime behavior.

## Detailed Design

### Extension status model

Extend `/parsidion-vault` output with a new section, for example:

- `Anthropic config:`
  - `ANTHROPIC_AUTH_TOKEN: env (bcb9…15Rl)`
  - `ANTHROPIC_BASE_URL: vault config (https://api.z.ai/api/anthropic)`
  - `API_TIMEOUT_MS: vault config (3000000)`
  - `ANTHROPIC_DEFAULT_HAIKU_MODEL: vault config (GLM-5-TURBO)`
  - `ANTHROPIC_DEFAULT_SONNET_MODEL: vault config (GLM-5.1)`
  - `ANTHROPIC_DEFAULT_OPUS_MODEL: vault config (GLM-5.1)`

Where each row reports:
- key
- effective source (`env`, `vault config`, `unset`)
- safe value preview

### Secrets masking

Sensitive values must be masked in extension status output.

Rules:
- `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and any future obvious secret-like value previews must be masked.
- Masking format should preserve only enough characters to identify which credential is active, e.g.:
  - `bcb9…15Rl`
  - or `set (masked)` if the token is too short to preview safely
- Non-secret values like base URLs, timeout integers, and model IDs may be shown in full.

### Vault config resolution for status display

The extension should inspect the same vault location conceptually used by the Python scripts:

1. `CLAUDE_VAULT` env var if set
2. default `~/ClaudeVault`

This is sufficient for status reporting in the extension.

For this feature, the extension does **not** need full named-vault or project-local `.claude/vault` resolution parity unless that parity is already easy to reuse without substantial complexity. If that parity can be added cheaply and clearly, it is acceptable; otherwise keep the status resolver simple and document its scope.

### Config parsing strategy

Preferred strategy: minimal read-only parsing in TypeScript for status reporting.

Constraints:
- the parser only needs to read the `anthropic_env` section and simple scalar values
- malformed or missing config must not crash the extension
- failures should degrade to a status note such as `vault config unreadable` or `not found`

This parser is for status display only, not runtime behavior.

### Effective precedence reporting

Per key:
1. if a live process env var exists and is non-empty, report `source = env`
2. else if `anthropic_env.<KEY>` exists in vault config, report `source = vault config`
3. else report `source = unset`

### Affected extension command

`/parsidion-vault` should continue showing:
- resolved script directory
- synthetic transcript path
- queued context chunks
- processed subagent count
- current session file

and add:
- Anthropic/GLM config status summary
- optional short note that Python hook scripts remain runtime source of truth

## Error Handling

The extension status path must be resilient:

### Missing vault config
- show `vault config: not found`
- each key falls back to `env` or `unset`

### Malformed config
- show a parse warning in status output
- do not throw or break the command UI
- continue reporting env-only values when possible

### Missing keys
- report those keys as `unset`

### Missing script dir
- preserve current status behavior
- Anthropic config status should still render independently when possible

## Files to Modify

### Primary implementation
- `extensions/pi/parsidion-vault/parsidion-vault.ts`

### Extension docs
- `extensions/pi/parsidion-vault/parsidion-vault.md`

### Repository docs
- `README.md`
- `docs/ARCHITECTURE.md`
- any pi-specific docs or integration references that mention extension status or env propagation

## Testing Strategy

### Unit/behavior tests

Add or extend tests to cover:
1. env value wins over vault config in status output
2. vault config is used when env is absent
3. unset values report correctly
4. secrets are masked
5. malformed/missing config does not crash status rendering

### Verification

Run repository verification:

```bash
make checkall
```

## Documentation Updates

Documentation should consistently explain:

1. `anthropic_env` lives in `~/ClaudeVault/config.yaml`
2. runtime precedence is:
   - real environment variable
   - vault `anthropic_env`
   - script default behavior
3. pi extension `/parsidion-vault` reports effective status but does not override Python runtime resolution
4. GLM/Z.AI-compatible example values are supported in `anthropic_env`

## Risks

1. **Drift between TS status parser and Python config parser**
   - mitigated by keeping TS parser narrow and read-only
2. **User confusion about source of truth**
   - mitigated by explicit status note: Python remains authoritative for runtime behavior
3. **Leaking secrets in status output**
   - mitigated by strict masking rules

## Acceptance Criteria

The design is complete when:

1. `/parsidion-vault` shows Anthropic-compatible config status
2. secret values are masked
3. status clearly indicates `env`, `vault config`, or `unset`
4. Python remains the runtime source of truth
5. repository docs describe this behavior consistently
6. `make checkall` passes after implementation
