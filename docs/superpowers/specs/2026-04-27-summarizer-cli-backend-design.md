# Summarizer CLI Backend Design

> **Note:** Historical design document. Path references and project names may reflect the legacy `parsidion-cc` name (renamed to `parsidion` in v0.7.0). Do not edit this document.

## Summary

Remove the direct `claude-agent-sdk` dependency from `summarize_sessions.py` and route summarizer model calls through Parsidion's backend-neutral CLI prompt layer. The summarizer will use `claude -p` or `codex exec` through `ai_backend.run_ai_prompt()`, selected by the same backend/model configuration introduced for prompt-style scripts. The existing queue, preprocessing, concurrency, write-gate, merge, note-writing, index rebuild, and git behavior remain intact.

## Goals

- Remove `claude-agent-sdk` from the summarizer PEP 723 dependencies and imports.
- Support Codex-backed session summarization via `codex exec` without adding Codex SDK dependencies.
- Keep Claude summarization support via the Claude CLI backend.
- Reuse backend-aware model defaults:
  - chunk/hierarchical summaries use the small tier
  - final note/write-gate generation uses the large tier
- Preserve all existing summarizer behavior around pending queue processing, stale transcript purging, write-gate decisions, merge decisions, dry-run, progress, index rebuild, and commits.
- Keep the async/concurrent summarizer structure using `anyio`.

## Non-Goals

- Do not add the Codex Python SDK in this phase.
- Do not add OpenAI API provider support.
- Do not change hook queueing behavior.
- Do not change the vault note prompt/output contract except where tests expose an existing bug.
- Do not remove `anyio`; it remains useful for structured concurrency and running blocking CLI calls in worker threads.

## Current State

`summarize_sessions.py` is a PEP 723 script with:

```python
# dependencies = ["claude-agent-sdk>=0.0.10,<1.0", "anyio>=4.0.0,<5.0"]
```

It imports:

```python
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
```

The SDK is used in two places:

1. `_summarize_chunk()` calls `query()` with a cheaper model to summarize chunks of long transcripts.
2. `summarize_one()` calls `query()` with the main model to generate the final vault-note payload or write-gate JSON decision.

Both usages are one-shot prompt-in/final-text-out calls with no tools and no need for streaming, custom tools, persistent sessions, or long-lived threads. This maps directly to the CLI prompt backend added in `ai_backend.py`.

## Target Architecture

Keep `summarize_sessions.py` as the orchestration module. Add small local helper functions inside it rather than introducing a new module unless implementation shows the file becoming unclear.

Proposed helper:

```python
async def _run_summarizer_prompt(
    prompt: str,
    *,
    model: str | None,
    model_tier: ai_backend.ModelTier,
    purpose: str,
    timeout: int | float | None,
    vault: Path,
) -> str | None:
    return await anyio.to_thread.run_sync(
        lambda: ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier=model_tier,
            timeout=timeout,
            purpose=purpose,
            vault=vault,
        )
    )
```

This keeps blocking subprocess work out of the event loop while preserving the existing `anyio.create_task_group()` concurrency model.

## Backend Selection

Use existing `ai_backend.resolve_ai_backend()` behavior:

- `ai.backend: codex-cli` uses `codex exec`.
- `ai.backend: claude-cli` uses `claude -p`.
- `ai.backend: auto` prefers runtime signals and falls back to Claude CLI when ambiguous.
- `ai.backend: none` disables AI calls; summarizer entries should fail/skip gracefully as current no-output behavior does.

No new `summarizer.backend` key is required for the first pass. The user explicitly wants a CLI-only backend and has already configured `ai.backend: codex-cli`. Keeping one backend switch avoids a second precedence model.

## Model Resolution

Stop defaulting summarizer model variables to hardcoded Claude IDs when the user has not explicitly configured a model.

Config semantics:

```yaml
summarizer:
  model: null          # null/absent = ai_models.<backend>.large
  cluster_model: null  # null/absent = ai_models.<backend>.small
```

Rules:

1. CLI `--model MODEL` remains an explicit large-model override.
2. `summarizer.model` is an explicit large-model override when it is a non-empty string.
3. Otherwise final note generation passes `model=None, model_tier="large"` to `ai_backend`.
4. `summarizer.cluster_model` is an explicit small-model override when it is a non-empty string.
5. Otherwise chunk summarization passes `model=None, model_tier="small"` to `ai_backend`.

With `ai.backend: codex-cli`, null/absent models resolve to `gpt-5.5` through `ai_models.codex.large` and `ai_models.codex.small`.

## Behavior Preservation

### Chunk summarization

Current behavior:

- For transcripts under `max_cleaned_chars`, use cleaned text unchanged.
- For long transcripts, split into chunks.
- Summarize each chunk.
- If chunk summarization fails, fall back to a truncated raw chunk.

New behavior:

- Same flow.
- `_summarize_chunk()` calls `_run_summarizer_prompt(..., model_tier="small", purpose="summarizer-chunk")`.
- If the backend returns `None`, fall back to `chunk_text[:500]` as today.

### Final note generation

Current behavior:

- Build prompt with transcript, categories, existing tags, and dedup candidates.
- Query SDK.
- Interpret JSON write-gate decisions for `skip` or `merge`.
- Otherwise parse/write markdown note.

New behavior:

- Same flow.
- `summarize_one()` calls `_run_summarizer_prompt(..., model_tier="large", purpose="summarizer-note")`.
- If backend returns `None`, keep current `No result ...` failure path.
- Existing JSON/markdown parsing remains unchanged.

### Persistence

The existing `--persist` flag currently affects Claude Agent SDK `extra_args`. CLI backends already control persistence differently:

- Claude CLI backend always uses `--no-session-persistence`.
- Codex CLI backend defaults to `--ephemeral` via `codex_cli.ephemeral: true`.

For this phase, keep `--persist` accepted for CLI compatibility but make it effectively ignored by the summarizer CLI backend. Document that `codex_cli.ephemeral` / backend configuration controls CLI persistence.

## Dependencies

Update the PEP 723 header:

```python
# dependencies = ["anyio>=4.0.0,<5.0"]
```

Remove direct imports from `claude_agent_sdk`.

## Testing Strategy

Add or update tests without invoking real Claude or Codex binaries.

Primary tests:

- `_summarize_chunk()` uses `ai_backend.run_ai_prompt()` through async thread bridge with `model_tier="small"` and `purpose="summarizer-chunk"`.
- `_summarize_chunk()` falls back to raw chunk text when backend returns `None`.
- `summarize_one()` uses `model_tier="large"` and `purpose="summarizer-note"`.
- `summarize_one()` preserves `skip` write-gate behavior.
- `summarize_one()` preserves normal markdown note writing behavior in dry-run mode or temporary vault.
- CLI/config model resolution does not pass Claude defaults when model config is null/absent.
- `summarize_sessions.py` no longer imports `claude_agent_sdk` and PEP 723 dependencies no longer include it.

Verification:

```bash
uv run pytest tests/test_summarize_sessions.py tests/test_ai_backend.py -q
make checkall
```

If no summarizer test file currently exists, create `tests/test_summarize_sessions.py` with focused unit tests that monkeypatch the backend helper and use small temporary transcripts/vaults.

## Documentation

Update:

- `README.md`
- `skills/parsidion/SKILL.md`
- `CHANGELOG.md`
- `skills/parsidion/templates/config.yaml`

Docs should say:

- `summarize_sessions.py` now uses the configured prompt AI backend.
- Codex summarization works through `codex exec` when `ai.backend: codex-cli`.
- No Codex SDK or Claude Agent SDK is required for the summarizer path.
- `summarizer.model: null` uses backend large default.
- `summarizer.cluster_model: null` uses backend small default.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| CLI subprocess overhead vs SDK calls | Existing summarizer already limits concurrency; keep `max_parallel` and backend timeouts configurable. |
| Codex CLI produces noisy output | `ai_backend` uses `--output-last-message` as authoritative result. |
| Claude model IDs leak into Codex summarizer | Pass `model=None` unless user explicitly configured a model. Add tests. |
| Event-loop blocking from CLI calls | Use `anyio.to_thread.run_sync`. |
| Loss of SDK-specific persistence semantics | Keep `--persist` accepted; document it as not meaningful for CLI backend in this phase. |

## Success Criteria

- `summarize_sessions.py` has no `claude_agent_sdk` import.
- PEP 723 dependencies no longer include `claude-agent-sdk`.
- Chunk summarization and final note generation use `ai_backend.run_ai_prompt` via async thread execution.
- Codex backend defaults resolve to `gpt-5.5` for small and large summarizer calls when configured.
- Existing summarizer queue/write behavior is preserved by tests.
- `make checkall` passes.
