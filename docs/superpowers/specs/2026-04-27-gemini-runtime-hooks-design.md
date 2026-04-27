# Gemini Runtime Hooks Design

## Summary

Add Gemini CLI as a first-class Parsidion runtime integration alongside Claude Code and Codex. The initial integration will install Gemini native hooks for session start and session end, add Gemini transcript discovery/parsing helpers, and update installer runtime selection so users can choose Claude, Codex, Gemini, all runtimes, or shared tooling only.

The first pass deliberately mirrors the low-risk Codex integration: load Parsidion context at session start and queue useful transcript summaries at session end. More invasive Gemini hooks such as `BeforeAgent`, `BeforeTool`, `AfterTool`, `BeforeModel`, and `PreCompress` remain future work.

## Goals

- Add `--runtime gemini` and `--runtime all` support to the installer.
- Update the interactive runtime menu to explicitly offer:
  - Claude
  - Codex
  - Gemini
  - Claude + Codex
  - all runtimes
  - shared tooling only / none
- Preserve non-interactive `--yes` default behavior: Claude only.
- Install Gemini hooks into `~/.gemini/settings.json` without overwriting unrelated settings or hooks.
- Uninstall only Parsidion-managed Gemini hook handlers when `--uninstall-hooks --runtime gemini` or equivalent is used.
- Add Gemini `SessionStart` and `SessionEnd` wrapper scripts.
- Add Gemini transcript path validation and assistant/model text parsing helpers.
- Reuse existing Parsidion context building, category detection, daily-note update, and pending summary queue logic.

## Non-Goals

- Do not implement per-turn `BeforeAgent` context injection in this phase.
- Do not implement `PreCompress` snapshots in this phase.
- Do not implement tool auditing or tool blocking through `BeforeTool` / `AfterTool`.
- Do not package Parsidion as a Gemini extension yet.
- Do not infer Gemini subagent lifecycle events yet; Gemini does not expose a direct equivalent to Claude `SubagentStop`.
- Do not add Gemini API or SDK calls for AI summarization. Existing prompt-style AI backend remains `claude-cli`, `codex-cli`, or `none`.

## Background

Gemini CLI v0.26.0+ provides a synchronous JSON stdin/stdout hook system. Hooks are configured in `settings.json` and support lifecycle, agent, model, tool, compression, and notification events.

Relevant lifecycle hooks for the first Parsidion integration:

- `SessionStart`: fires on startup, resume, or clear. It can inject context through `hookSpecificOutput.additionalContext`.
- `SessionEnd`: fires when the CLI exits or a session is cleared. It is best-effort and suitable for cleanup/queuing work.

Gemini hook input includes common fields such as:

```json
{
  "session_id": "...",
  "transcript_path": "/path/to/transcript.jsonl",
  "cwd": "/path/to/project",
  "hook_event_name": "SessionStart",
  "timestamp": "..."
}
```

Gemini hook output must be strict JSON on stdout. Logs and debug output must go to stderr.

## Runtime Selection

Current installer choices are `claude`, `codex`, `both`, and `none`, where `both` means Claude + Codex. Gemini introduces a third runtime, so the runtime model becomes:

```text
claude | codex | gemini | both | all | none
```

Semantics:

- `claude`: install/uninstall Claude Code runtime hooks only.
- `codex`: install/uninstall Codex runtime hooks only.
- `gemini`: install/uninstall Gemini CLI runtime hooks only.
- `both`: backwards-compatible alias for Claude + Codex.
- `all`: Claude + Codex + Gemini.
- `none`: shared tooling only; no runtime hooks.

Defaults:

- `--yes` or non-interactive install continues to default to `claude` for backwards compatibility.
- Interactive install presents an explicit menu with Claude, Codex, Gemini, Claude + Codex, all, and none. The default should remain `both` unless the user chooses otherwise, so existing interactive expectations are not silently expanded.

## Gemini Hook Installation

Gemini hooks are installed into:

```text
~/.gemini/settings.json
```

The installer must preserve unrelated Gemini settings and unrelated hook handlers. It should create the file when absent and merge only Parsidion-managed handlers when missing.

Initial managed hooks:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "*",
        "hooks": [
          {
            "name": "parsidion-session-start",
            "type": "command",
            "command": "uv run --no-project ~/.claude/skills/parsidion/scripts/gemini_session_start_hook.py",
            "timeout": 10000
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "name": "parsidion-session-end",
            "type": "command",
            "command": "uv run --no-project ~/.claude/skills/parsidion/scripts/gemini_session_end_hook.py",
            "timeout": 10000
          }
        ]
      }
    ]
  }
}
```

Matcher rationale:

- Gemini docs state lifecycle matchers are exact strings, with `"*"` or `""` matching all occurrences.
- Use `"*"` for both lifecycle hooks unless tests against Gemini fixtures show `""` is safer.

Uninstall behavior:

- Remove only hook configurations whose `command` equals the Parsidion-managed Gemini hook command.
- Preserve malformed or unrelated entries where safe, matching hardened Codex/Claude uninstall behavior.
- Remove empty Parsidion-only hook groups after handler removal.

## Hook Scripts

### `gemini_session_start_hook.py`

Responsibilities:

1. Read JSON payload from stdin. Invalid or non-object JSON becomes `{}`.
2. Determine `cwd` from payload or `Path.cwd()`.
3. Resolve `session_start_hook.max_chars` from config.
4. Temporarily set `PARSIDION_RUNTIME=gemini` while building context.
5. Call existing `session_start_hook.build_session_context(...)` with AI disabled for the initial integration.
6. Emit Gemini-compatible JSON:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "..."
  }
}
```

7. On any exception, print traceback to stderr and emit `{}`.

Notes:

- AI selection remains disabled here initially, matching Codex `SessionStart` wrapper behavior. The default startup path stays fast and avoids recursive CLI calls.
- `systemMessage` can be left unused in this phase.

### `gemini_session_end_hook.py`

Responsibilities:

1. Read JSON payload from stdin. Invalid or non-object JSON becomes `{}`.
2. Skip immediately when `PARSIDION_INTERNAL` is set.
3. Extract `cwd`, `transcript_path`, `session_id`, and `reason` when present.
4. Require `transcript_path` to exist and be under an allowed transcript root.
5. Require `is_gemini_transcript_path(transcript_path, cwd=cwd)`.
6. Resolve vault path from `cwd` and ensure vault directories.
7. Read the last configured number of transcript lines.
8. Parse assistant/model text using `parse_gemini_transcript_lines(...)`.
9. Detect categories using existing `detect_categories(...)`.
10. If categories exist:
    - append a compact summary to the daily note
    - append the transcript to `pending_summaries.jsonl`
11. Emit `{}` on stdout for all success/skip paths.
12. On any exception, print traceback to stderr and emit `{}`.

SessionEnd is best-effort in Gemini, so the hook should stay quick and should not block or run summarization inline.

## Gemini Transcript Support

Add helpers to `vault_hooks.py` and re-export through `vault_common.py`:

- `gemini_home() -> Path`
- `is_gemini_transcript_path(transcript_path: Path, cwd: str | None = None) -> bool`
- `parse_gemini_transcript_lines(lines: list[str]) -> list[str]`

Update `allowed_transcript_roots(cwd)` to include Gemini roots:

- `~/.gemini`
- `<cwd>/.gemini` when `cwd` is provided

The exact transcript subdirectory can vary by Gemini version/configuration, so the first pass allows paths under the Gemini settings roots. This matches Gemini's own project/user settings model while still preventing arbitrary transcript paths outside Gemini-managed directories.

### Parser Shapes

The parser should be permissive and ignore unknown records. It should extract text from likely Gemini records including:

1. Direct role records:

```json
{"role": "model", "content": "..."}
```

2. Type-based records:

```json
{"type": "model", "content": "..."}
{"type": "assistant", "content": "..."}
```

3. Message wrapper records:

```json
{"message": {"role": "model", "content": "..."}}
```

4. Gemini model response records:

```json
{
  "llm_response": {
    "candidates": [
      {"content": {"role": "model", "parts": ["text", {"text": "more"}]}}
    ]
  }
}
```

5. Content arrays using text blocks:

```json
{"role": "model", "content": [{"type": "text", "text": "..."}]}
```

Only model/assistant output should be returned. User prompts and tool results should be ignored.

## Config and Runtime Hints

When Gemini hooks run, they should set:

```text
PARSIDION_RUNTIME=gemini
```

Current `ai_backend.resolve_ai_backend(...)` supports Claude and Codex runtime hints. For this phase, Gemini should not become a prompt AI backend because there is no Gemini prompt backend in Parsidion yet. Therefore:

- `PARSIDION_RUNTIME=gemini` should not select a new AI backend.
- Existing `ai.backend` config still controls prompt AI calls.
- If `ai.backend: auto` and only Gemini is detected, fallback should remain `claude-cli` unless a future Gemini prompt backend is added.

Docs should make this explicit: Gemini runtime hooks are independent from prompt AI backend selection.

## Tests

Add or update tests in these areas:

### Installer tests

- `--runtime gemini` parses successfully.
- `--runtime all` parses successfully.
- `resolve_runtime_choice(..., yes=True)` still returns `claude`.
- Interactive parsing recognizes Gemini/all menu inputs.
- Gemini hook merge creates `~/.gemini/settings.json` when absent.
- Gemini hook merge preserves unrelated settings and hooks.
- Gemini hook merge is idempotent.
- Gemini hook removal removes only Parsidion-managed commands.
- Runtime install flow for `gemini` registers only Gemini hooks.
- Runtime install flow for `all` includes Claude, Codex, and Gemini hook plans.
- Runtime uninstall flow for `gemini` leaves Claude/Codex hooks alone.

### Transcript helper tests

- `allowed_transcript_roots(cwd)` includes user and project Gemini roots.
- `is_gemini_transcript_path(...)` accepts paths under `~/.gemini` and `<cwd>/.gemini`.
- `parse_gemini_transcript_lines(...)` extracts direct role/model records.
- Parser extracts message-wrapper model records.
- Parser extracts `llm_response.candidates[].content.parts[]` text.
- Parser ignores user records, tool records, malformed JSON, and unknown shapes.

### Hook integration tests

- Gemini SessionStart emits valid JSON with `hookSpecificOutput.hookEventName == "SessionStart"` and `additionalContext`.
- Gemini SessionStart sets/restores `PARSIDION_RUNTIME`.
- Gemini SessionEnd exits cleanly on missing/invalid transcript path.
- Gemini SessionEnd with a valid Gemini transcript queues pending summaries when categories are detected.
- Gemini SessionEnd skips when `PARSIDION_INTERNAL` is set.

## Documentation

Update:

- `README.md`
- `skills/parsidion/SKILL.md`
- `docs/ARCHITECTURE.md`
- `CHANGELOG.md`
- installer help text

Docs should state:

- Gemini CLI hooks are supported for `SessionStart` and `SessionEnd`.
- Install with `--runtime gemini` or `--runtime all`.
- Gemini hook config is written to `~/.gemini/settings.json`.
- Gemini runtime hooks do not imply a Gemini prompt AI backend.
- Subagent lifecycle capture is not native in the first pass.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Gemini transcript schema differs across versions | Parser is permissive and fixtures cover multiple documented/observed shapes. Unknown records are ignored. |
| SessionEnd is best-effort and may not finish long work | Do only lightweight parsing/queueing; summarization remains detached/on-demand. |
| Hook installation overwrites user Gemini settings | Merge JSON surgically; preserve unrelated/malformed entries where safe. |
| Runtime naming ambiguity with existing `both` | Keep `both` as Claude+Codex for compatibility; add `all` for Claude+Codex+Gemini. |
| Users expect Gemini to be an AI backend | Document that Gemini runtime hooks are separate from prompt AI backend selection. |

## Success Criteria

- `uv run python install.py --runtime gemini --dry-run` shows Gemini hook registration and no Claude/Codex hook registration.
- `uv run python install.py --runtime all --dry-run` shows Claude, Codex, and Gemini hook registration.
- Gemini hook scripts always emit valid JSON on stdout.
- Gemini `SessionStart` can inject Parsidion context.
- Gemini `SessionEnd` can parse a Gemini transcript fixture and queue pending summaries.
- Existing Claude and Codex behavior remains unchanged.
- Full verification passes with `make checkall`.
