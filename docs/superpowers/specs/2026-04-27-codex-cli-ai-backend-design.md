# Codex CLI AI Backend Design

## Summary

Add a runtime-neutral prompt AI backend for Parsidion scripts that currently shell out to `claude -p`. The new backend layer will support Claude CLI and Codex CLI, choose the active runtime in `auto` mode, and use purpose-aware model defaults so Codex mode never receives Claude model IDs. This change intentionally excludes `summarize_sessions.py` because it uses `claude-agent-sdk`; SDK replacement will be handled separately after prompt-style CLI calls work.

## Goals

- Add configurable AI backend selection for prompt-style Parsidion scripts.
- Support `codex exec` as the Codex CLI backend equivalent to `claude -p`.
- Preserve existing Claude behavior when running under Claude or when runtime is ambiguous.
- Make model defaults backend-aware and task-size-aware.
- Avoid passing Claude model IDs to Codex.
- Keep Codex calls low-risk by defaulting to read-only, ephemeral, non-persistent runs.
- Keep existing non-AI fallback paths working when AI calls fail.

## Non-Goals

- Do not replace `claude-agent-sdk` usage in `summarize_sessions.py` in this phase.
- Do not add OpenAI API provider support.
- Do not manage, copy, inspect, or depend on `~/.codex/auth.json` beyond letting Codex CLI use its normal auth path.
- Do not make Codex CLI behavior a drop-in replacement for every Claude SDK capability.
- Do not enable mutating Codex AI calls by default.

## Current State

Prompt-style AI call sites are currently hardcoded to Claude CLI:

- `session_start_hook.py --ai` uses `claude -p` for note selection.
- `session_stop_hook.py --ai` uses `claude -p` for transcript classification.
- `vault_doctor.py` uses `claude -p` for frontmatter repair and prefix-cluster filtering.
- `vault_merge.py` uses `claude -p` for merge synthesis.
- `run_trigger_eval.py` uses `claude -p` for trigger evaluation.
- `embed_eval_generate.py` uses `claude -p` for eval data generation.
- `vault_links.py` has Claude-adjacent subprocess usage that should be checked during implementation.

Codex runtime hooks now exist for lifecycle integration:

- `codex_session_start_hook.py` builds non-AI context and emits Codex-compatible `hookSpecificOutput`.
- `codex_stop_hook.py` parses Codex transcripts and queues useful sessions.

These hooks can feed the Parsidion queue, but prompt-style AI scripts still require Claude CLI.

## Config Model

Add a new global backend selection section:

```yaml
ai:
  backend: auto        # auto | claude-cli | codex-cli | none
```

Add purpose-aware model defaults:

```yaml
ai_models:
  claude:
    small: claude-haiku-4-5-20251001
    large: claude-sonnet-4-6
  codex:
    small: gpt-5.5
    large: gpt-5.5
```

Add optional Codex CLI invocation config:

```yaml
codex_cli:
  command: codex
  timeout: 60
  sandbox: read-only
  ephemeral: true
  skip_git_repo_check: true
```

Existing per-script options such as `session_start_hook.ai_model` and `session_stop_hook.ai_model` remain supported as explicit overrides. However, implementation must avoid treating existing Claude default model IDs as backend-neutral. If no explicit model is configured, scripts should pass a model tier (`small` or `large`) and let the backend resolve the correct model for the chosen backend.

## Backend Resolution

`ai.backend` values:

| Value | Behavior |
|---|---|
| `auto` | Prefer the current runtime when detectable; otherwise preserve existing Claude behavior. |
| `claude-cli` | Use `claude -p`. |
| `codex-cli` | Use `codex exec`. |
| `none` | Disable prompt-style AI and return `None` so callers use existing fallback paths. |

`auto` runtime detection order:

1. `PARSIDION_RUNTIME=codex` -> `codex-cli`.
2. `PARSIDION_RUNTIME=claude` -> `claude-cli`.
3. Strong Codex environment signal, such as `CODEX_SANDBOX`, `CODEX_SESSION_ID`, or another verified Codex-specific runtime variable discovered during implementation -> `codex-cli`.
4. `CLAUDECODE` set -> `claude-cli`.
5. Ambiguous environment -> `claude-cli` for backwards compatibility.

`CODEX_HOME` alone is not sufficient to prove the current runtime is Codex because users may set it globally. It may be used only as weak supporting evidence if paired with another Codex-specific runtime signal.

Codex wrapper hooks should set `PARSIDION_RUNTIME=codex` before invoking shared context or AI code. Claude hooks may set `PARSIDION_RUNTIME=claude` where useful, but existing `CLAUDECODE` detection is enough for backwards compatibility.

## Model Resolution

Model choice is separate from backend choice.

The shared helper will accept:

```python
model: str | None = None
model_tier: Literal["small", "large"] = "small"
```

Resolution rules:

1. If a caller passes an explicit non-empty `model`, use it as-is.
2. If backend is `claude-cli`, use `ai_models.claude.<tier>`.
3. If backend is `codex-cli`, use `ai_models.codex.<tier>`.
4. If backend is `none`, do not resolve a model.

Default tier mapping:

| Task | Tier | Rationale |
|---|---|---|
| SessionStart note selection | `small` | Fast selection/ranking task. |
| SessionStop classification | `small` | JSON classification over condensed text. |
| Vault doctor frontmatter repair | `small` | Structured cleanup. |
| Vault doctor prefix filtering | `small` | Classification/filtering. |
| Trigger eval | `small` | Evaluation/classification. |
| Embed eval generation | `small` | Dev/test data generation unless a caller explicitly requests otherwise. |
| Vault merge synthesis | `large` | Requires synthesis and careful merging. |

Codex defaults use `gpt-5.5` for both tiers initially, with the important requirement that larger synthesis tasks use `gpt-5.5`. Users can later configure a cheaper Codex `small` model without changing call sites.

Existing legacy defaults under `defaults.haiku_model` and `defaults.sonnet_model` remain for Claude compatibility, but new backend-aware defaults should prefer `ai_models.*` when present. During migration, call sites should stop passing Claude default constants into backend-neutral helpers unless the model was explicitly supplied by CLI/config.

## Shared Helper

Create:

```text
skills/parsidion/scripts/ai_backend.py
```

Public API:

```python
from pathlib import Path
from typing import Literal

AiBackend = Literal["claude-cli", "codex-cli", "none"]
ModelTier = Literal["small", "large"]


def resolve_ai_backend(vault: Path | None = None) -> AiBackend:
    """Resolve configured backend, applying auto runtime detection."""


def resolve_ai_model(
    backend: AiBackend,
    *,
    model: str | None = None,
    model_tier: ModelTier = "small",
    vault: Path | None = None,
) -> str | None:
    """Resolve an explicit or backend-specific default model."""


def run_ai_prompt(
    prompt: str,
    *,
    model: str | None = None,
    model_tier: ModelTier = "small",
    timeout: int | float | None = None,
    cwd: str | Path | None = None,
    purpose: str = "general",
    vault: Path | None = None,
) -> str | None:
    """Run a prompt through the configured backend and return final text."""
```

The helper should return `None` on backend disabled, missing executable, timeout, non-zero exit, empty output, or unreadable output file. Callers already have fallback paths and should not need to catch backend-specific failures.

## Claude CLI Backend

Command shape:

```text
claude -p <prompt> --model <model> --no-session-persistence
```

If `model` resolves to `None`, omit `--model`.

Environment:

- Use `vault_common.env_without_claudecode(vault=vault)`.
- Preserve `PARSIDION_INTERNAL=1` so hooks skip internally launched sessions.
- Continue excluding `CLAUDECODE` to avoid nested Claude guard failures.

Timeout:

- Use caller timeout when supplied.
- Otherwise use relevant per-script config if caller passes it.
- Otherwise use a safe helper default.

Output:

- Return stripped stdout.
- Strip markdown fences only in caller code when the caller already does so for its expected schema.

## Codex CLI Backend

Command shape:

```text
codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  --output-last-message <tmpfile> \
  --model <model> \
  <prompt>
```

Config controls:

- `codex_cli.command` replaces `codex` when set.
- `codex_cli.ephemeral=false` omits `--ephemeral`.
- `codex_cli.sandbox` controls `--sandbox`; default `read-only`.
- `codex_cli.skip_git_repo_check=false` omits `--skip-git-repo-check`.
- `codex_cli.timeout` is the default timeout for Codex calls when caller does not supply one.

Output:

- Use `--output-last-message` as authoritative final text.
- Capture stdout/stderr for diagnostics if needed, but do not parse event streams by default.
- Delete the temporary output file after reading it.

Environment:

- Use a filtered environment similar to `env_without_claudecode`; include `PATH`, `HOME`, `USER`, `SHELL`, `TERM`, locale variables, proxy variables, `CODEX_HOME`, and `PARSIDION_RUNTIME` when present.
- Set `PARSIDION_INTERNAL=1` so Codex hooks skip internally launched Codex sessions.
- Do not pass the full process environment by default.
- Do not read or modify `~/.codex/auth.json`.

Safety:

- Default to `--ephemeral` to avoid generating additional session rollout files.
- Default to `--sandbox read-only` to prevent AI backend calls from mutating the workspace.
- Use `--skip-git-repo-check` because scripts may run against vault directories or temporary files outside a git repository.

## Script Migration

Migrate direct `claude -p` subprocess callers to `ai_backend.run_ai_prompt()`.

Expected tier choices:

- `session_start_hook.py`: `model_tier="small"`.
- `session_stop_hook.py`: `model_tier="small"`.
- `vault_doctor.py` repair/filter helpers: `model_tier="small"`.
- `vault_merge.py`: `model_tier="large"`.
- `run_trigger_eval.py`: `model_tier="small"`.
- `embed_eval_generate.py`: `model_tier="small"`.

Explicit user-supplied model flags remain supported. If a CLI option currently defaults to a Claude model constant, implementation should change the default to `None` where possible and let the backend resolve the tier default. If changing a public CLI default would break help text or existing behavior, use a sentinel to distinguish “not supplied” from explicit model.

`codex_session_start_hook.py` should continue to default to non-AI context. If users enable `session_start_hook.ai_model` or a future AI toggle, it can use the shared backend with `PARSIDION_RUNTIME=codex`, but this phase should not silently turn on AI for Codex hooks.

## Error Handling

- Missing `codex` executable returns `None` and logs concise debug output only where existing scripts already log AI failures.
- Non-zero `codex exec` returns `None`.
- Timeout kills the process and returns `None`.
- Empty output file returns `None`.
- Invalid `ai.backend` config should warn and fall back to `claude-cli` for backwards compatibility, or be rejected by config validation if the project pattern supports warnings.
- Invalid `ai_models` values fall back to hardcoded defaults for that backend/tier.

## Testing Strategy

Use TDD. New tests should avoid invoking real Claude or Codex binaries; monkeypatch subprocess calls.

Add tests for the helper:

- `resolve_ai_backend()` returns `codex-cli` when `PARSIDION_RUNTIME=codex` and `ai.backend=auto`.
- `resolve_ai_backend()` returns `claude-cli` when `PARSIDION_RUNTIME=claude` and `ai.backend=auto`.
- `resolve_ai_backend()` returns `claude-cli` when ambiguous and `ai.backend=auto`.
- `resolve_ai_backend()` returns configured explicit backend.
- `resolve_ai_model()` maps Claude small/large defaults.
- `resolve_ai_model()` maps Codex small/large defaults to `gpt-5.5` by default.
- Explicit model overrides backend defaults.
- `run_ai_prompt()` builds the expected Claude command.
- `run_ai_prompt()` builds the expected Codex command with `--ephemeral`, `--sandbox read-only`, `--skip-git-repo-check`, and `--output-last-message`.
- Codex output is read from the output-last-message file.
- Codex failure, timeout, or empty output returns `None`.

Add script-level tests for representative migrations:

- `session_stop_hook.py` AI classification calls `run_ai_prompt(..., model_tier="small")`.
- `vault_merge.py` calls `run_ai_prompt(..., model_tier="large")`.
- `codex_session_start_hook.py` sets or preserves `PARSIDION_RUNTIME=codex` where backend detection depends on it.

Run verification:

```text
uv run pytest tests/test_ai_backend.py tests/test_session_stop_hook.py tests/test_vault_doctor.py tests/test_hook_integration.py
make checkall
```

## Documentation

Update:

- `README.md` configuration section with `ai`, `ai_models`, and `codex_cli` examples.
- `skills/parsidion/SKILL.md` configuration section with backend behavior.
- `CHANGELOG.md` with Codex CLI AI backend support.

Docs must clearly state:

- Codex CLI backend uses `codex exec`, not OpenAI API credentials.
- Parsidion does not manage Codex auth files.
- `summarize_sessions.py` still uses Claude Agent SDK until a later phase.
- Codex backend defaults to `gpt-5.5`, especially for larger synthesis tasks.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Codex backend mutates files unexpectedly | Default `--sandbox read-only`; keep calls prompt-style and output-file based. |
| Codex backend recursively triggers Parsidion hooks | Set `PARSIDION_INTERNAL=1`; Codex hooks already skip internal sessions. |
| Claude model IDs leak into Codex calls | Resolve model after backend selection using `ai_models.<backend>.<tier>`. |
| Users expect summarizer to use Codex | Document that SDK summarizer remains Claude-backed in this phase. |
| `auto` selects Codex too broadly | Require explicit runtime hint or strong Codex runtime env; do not use `CODEX_HOME` alone. |
| Codex CLI output stream is noisy | Use `--output-last-message` as authoritative result. |

## Success Criteria

- Users can set `ai.backend: codex-cli` and prompt-style scripts use `codex exec`.
- In `auto`, Codex runtime hints select Codex and Claude runtime hints select Claude.
- Ambiguous environments preserve current Claude behavior.
- Codex mode uses `gpt-5.5` defaults instead of Claude model IDs.
- Existing fallback behavior works when the configured backend fails.
- Full test suite passes.
