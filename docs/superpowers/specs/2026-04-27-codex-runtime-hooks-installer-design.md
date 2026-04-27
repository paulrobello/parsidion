# Codex Runtime Hooks and Installer Selection Design

Date: 2026-04-27

## Summary

Add first-class installer support for choosing which coding-agent runtime Parsidion integrates with, and add a low-risk Codex runtime adapter using native Codex hooks. The installer will support Claude, Codex, both, or shared tooling only. Interactive installs default to both Claude and Codex; non-interactive `--yes` installs stay backwards-compatible by defaulting to Claude only unless a new runtime flag is provided.

This is a runtime integration feature, not an OpenAI provider feature. Codex uses its own CLI/auth/subscription path. Parsidion reads hook payloads and transcripts; Parsidion does not manage `~/.codex/auth.json` and does not treat a Codex subscription as an OpenAI API credential.

## Goals

- Add explicit runtime selection to install and uninstall flows.
- Preserve existing Claude install behavior by default for `--yes` and existing scripts.
- Add useful Codex hook integration for session context and transcript queueing.
- Keep Codex code separate from Claude-specific hook registration and output shapes.
- Make installs and uninstalls idempotent and surgical, preserving unrelated user hooks.
- Document Codex hook limitations and setup requirements.

## Non-goals

- Do not implement an OpenAI LLM provider in this change.
- Do not drive Codex as an agent through `@openai/codex-sdk`.
- Do not try to reach Claude Code hook parity for Codex.
- Do not manage or copy Codex auth files.
- Do not install experimental Codex tool hooks beyond the useful session lifecycle hooks.

## Runtime Selection Model

Introduce a runtime option with four values:

| Runtime | Meaning |
|---|---|
| `claude` | Install/uninstall Claude Code assets and hooks only. |
| `codex` | Install/uninstall Codex hooks only, plus shared vault assets needed by those hooks. |
| `both` | Install/uninstall both Claude and Codex integrations. |
| `none` | Install shared vault tooling/assets only; do not register runtime hooks. |

Installer behavior:

- Add `--runtime claude|codex|both|none`.
- Interactive install prompt default: `both`.
- `--yes` default: `claude`, preserving current non-interactive behavior.
- `--skip-hooks` still suppresses runtime hook registration for all runtimes.
- `--skip-agent` only affects Claude agents; Codex has no Parsidion agent files in this phase.
- Installation plan output must clearly show selected runtimes and target config files.

Uninstall behavior:

- Use the same runtime selection for uninstall and hook-only uninstall.
- Interactive uninstall prompt default: `both`.
- `--yes --uninstall` default: `claude`, preserving existing automation semantics unless `--runtime` is supplied.
- `--runtime none --uninstall-hooks` is a no-op with a clear message.

## Claude Runtime Behavior

Claude behavior remains the current implementation unless excluded by `--runtime`:

- Skill installed under `~/.claude/skills/parsidion`.
- Agents installed under `~/.claude/agents` unless `--skip-agent` is set.
- Existing hooks registered in `~/.claude/settings.json`:
  - `SessionStart`
  - `SessionEnd`
  - `PreCompact`
  - `PostCompact`
  - `SubagentStop`
- `CLAUDE-VAULT.md` is copied into `~/.claude` and imported from `CLAUDE.md` when available.

The current Claude hook merge and cleanup logic should remain intact and continue to preserve unrelated hooks.

## Codex Runtime Behavior

Codex support is a separate runtime adapter. It should not write Claude `settings.json` entries.

Codex installer targets:

- `CODEX_HOME`, defaulting to `~/.codex`.
- `~/.codex/hooks.json` for hook registration.
- `~/.codex/config.toml` for `codex_hooks = true` enablement guidance or insertion.

Initial Codex hooks:

| Codex event | Parsidion script | Purpose |
|---|---|---|
| `SessionStart` | `codex_session_start_hook.py` | Resolve vault context and return it in Codex-compatible output. |
| `Stop` | `codex_stop_hook.py` | Validate `transcript_path`, parse useful transcript text, update daily note, and queue pending summarization. |

Do not install Codex `PreToolUse`, `PostToolUse`, or `UserPromptSubmit` in this phase. Codex tool hooks are currently too limited/noisy for safe default behavior, and prompt-time injection can be added after session lifecycle hooks are proven.

## Codex Hook Registration

Codex hooks use `hooks.json` under `CODEX_HOME` and have a nested hook schema. Parsidion should merge into existing files without overwriting unrelated hooks.

Managed command shape should be deterministic and identifiable, for example:

```text
uv run --no-project ~/.claude/skills/parsidion/scripts/codex_session_start_hook.py
uv run --no-project ~/.claude/skills/parsidion/scripts/codex_stop_hook.py
```

The implementation should use exact command matching plus event names for uninstall. It should not remove user hooks that merely mention Parsidion in a wrapper command.

Codex hook entries should include a Parsidion-identifying description/comment field when supported by the JSON structure, but uninstall must not depend on comments alone because Codex hook execution only requires command handlers.

If `~/.codex/hooks.json` is missing, create a minimal valid file. If it exists and contains invalid JSON, warn and skip Codex hook modification rather than overwriting user data.

## Codex Config Enablement

Codex hooks require `codex_hooks = true`. Installer behavior should be conservative:

- If `~/.codex/config.toml` does not exist, create it with:

```toml
[features]
codex_hooks = true
```

- If `[features]` exists and `codex_hooks` is absent, insert `codex_hooks = true` in that section.
- If `codex_hooks = false`, prompt before changing it interactively; with `--yes --runtime codex|both`, change it to `true` and print the change.
- If parsing is ambiguous, warn with the exact manual snippet and do not rewrite the file.

This can be implemented with a small stdlib text updater; do not add a TOML dependency just for installer config edits.

## Codex Hook Input and Output

Codex hook payloads include common fields such as:

- `session_id`
- `cwd`
- `model`
- `permission_mode`
- `hook_event_name`
- `transcript_path`
- `turn_id` for turn-scoped events

`transcript_path` may be null. Hooks must handle missing transcripts gracefully and output valid JSON.

`codex_session_start_hook.py` should reuse the existing context-building logic but emit Codex-compatible output. If Codex accepts a simple additional-context field, use that documented shape. If Codex output schema differs from Claude Code, keep the mapping isolated in the Codex wrapper.

`codex_stop_hook.py` should reuse Parsidion's session queueing concepts but support Codex rollout JSONL records. It should:

1. Read stdin JSON.
2. Skip internal Parsidion sessions.
3. Validate `transcript_path` exists and is under an allowed root.
4. Parse assistant/model output from Codex JSONL rollout records.
5. Detect categories using existing heuristics where possible.
6. Append a daily note summary and queue `pending_summaries.jsonl` when useful content is found.
7. Return `{}` on success or skip.

## Transcript Path Security

Extend transcript path validation to include Codex roots:

- `CODEX_HOME/sessions`, defaulting to `~/.codex/sessions`.
- Existing Claude and pi roots remain supported.

Add helper functions analogous to the pi helpers:

- `allowed_transcript_roots()` includes Codex roots.
- `is_codex_transcript_path(path)` identifies Codex transcripts.

Validation must resolve paths before comparing roots and must continue rejecting arbitrary filesystem paths.

## Transcript Parsing

Codex rollout JSONL differs from Claude Code and pi transcripts. Add a focused parser that handles real fixture-shaped records and ignores unknown records.

Parser requirements:

- Accept a list of JSONL lines.
- Ignore malformed lines.
- Extract assistant/model text from known Codex rollout message records.
- Ignore user-only and tool-only records for category detection unless future fixtures prove they are useful.
- Return a list of assistant text chunks compatible with existing `detect_categories()`.

Tests should use small fixture lines derived from observed Codex rollout schemas and should include malformed/unknown records.

## Installer User Experience

Interactive install should include a runtime section before optional feature prompts:

```text
Runtime Integrations
  Claude Code: ~/.claude settings, skills, agents, and hooks.
  Codex CLI: ~/.codex hooks for SessionStart and Stop.
  Shared tooling only: vault files/tools without runtime hooks.

Install runtime integrations? [both]
  1. Claude only
  2. Codex only
  3. Both Claude + Codex
  4. Shared tooling only
```

The final installation plan should show:

- selected runtime(s)
- Claude config dir when Claude is selected
- Codex home when Codex is selected
- hook events registered per runtime
- skipped runtime hooks when `none` or `--skip-hooks` is selected

## CLI Flags

Add:

```text
--runtime {claude,codex,both,none}
--codex-home PATH
```

`--codex-home` defaults to `$CODEX_HOME` when set, else `~/.codex`.

Existing `--claude-dir` remains for Claude only. It is still used as the install location for shared Parsidion scripts in this phase because the current skill/scripts layout lives under `~/.claude/skills/parsidion`. A later runtime-neutral install location can move scripts under `~/.local/share/parsidion` or similar.

## Error Handling

- Invalid `--runtime` values are rejected by argparse.
- Missing `uv` does not crash install; warn as existing code does for related features.
- Invalid Codex `hooks.json` warns and skips Codex hook changes.
- Ambiguous Codex `config.toml` warns and prints manual instructions.
- All hooks return valid JSON on errors to avoid blocking agent sessions.

## Testing

Add tests before implementation.

Installer tests:

- `parse_args()` accepts `--runtime` and `--codex-home`.
- Interactive/default runtime resolution returns `both` for prompts and `claude` for `--yes` when no runtime is supplied.
- Codex hook merge creates `hooks.json` without removing existing hooks.
- Codex hook merge is idempotent.
- Codex hook uninstall removes only Parsidion-managed Codex hooks.
- Codex config enablement creates or updates `[features] codex_hooks = true`.
- `--runtime none` skips runtime hook registration.

Hook tests:

- `codex_session_start_hook.py` emits valid JSON for minimal payloads.
- `codex_stop_hook.py` exits cleanly for missing/null transcript paths.
- `codex_stop_hook.py` rejects transcript paths outside allowed roots.
- Codex transcript parser extracts assistant text from fixture JSONL.
- Codex stop hook queues useful transcript content into `pending_summaries.jsonl`.

Verification commands:

```bash
uv run pytest tests/test_install.py tests/test_hook_integration.py tests/test_vault_common.py
make checkall
```

## Documentation Updates

Update:

- `README.md` install section with runtime choices and `--runtime` examples.
- `SECURITY.md` to mention Codex config surfaces under `~/.codex`.
- `skills/parsidion/SKILL.md` if user-facing hook behavior changes.
- `CHANGELOG.md` with Codex runtime adapter and installer runtime selection notes.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Codex hook schema changes | Keep Codex integration isolated in wrapper scripts and fixture-backed tests. |
| Codex transcript parser misses useful text | Use conservative parser, skip unknown records, and expand with real fixtures over time. |
| Installer corrupts user Codex hooks | Merge JSON surgically; skip invalid JSON; uninstall by exact managed commands only. |
| `codex_hooks = true` edit damages config | Use conservative text edits; warn and print manual instructions when ambiguous. |
| Users expect OpenAI provider support | Docs explicitly separate Codex runtime auth from Parsidion OpenAI API provider work. |

## Success Criteria

- Interactive install can select both Claude and Codex.
- `--yes` install remains Claude-only unless `--runtime` is supplied.
- Codex `SessionStart` and `Stop` hooks can be installed, skipped, and uninstalled idempotently.
- Codex stop hook can queue useful Codex transcript content for summarization.
- Existing Claude install/uninstall tests continue passing.
- `make checkall` passes.
