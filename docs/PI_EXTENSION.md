# pi Extension Install

Install, configure, and smoke-test the Parsidion extension for the [pi](https://github.com/badlogic/pi-mono) coding agent (and for [omp](https://github.com/badlogic/pi-mono), which loads the same extension — the `@mariozechner/*` npm scope and the `badlogic/pi-mono` repository are the same author). The pi adapter is a TypeScript extension that shells out to Parsidion's Python hook scripts, so the same vault and queue path used by Claude Code, Codex CLI, and Gemini CLI works for pi sessions too.

## Table of Contents

- [Overview](#overview)
- [Install](#install)
- [omp (oh my pi)](#omp-oh-my-pi)
- [Effective Anthropic / GLM Configuration](#effective-anthropic--glm-configuration)
- [Smoke Tests](#smoke-tests)
- [Troubleshooting](#troubleshooting)
- [Related Documentation](#related-documentation)

## Overview

The pi adapter extension source is included in the Parsidion repo at:

- `extensions/pi/parsidion/parsidion.ts`
- `extensions/pi/parsidion/parsidion.md`

It registers Parsidion's SessionStart, SessionEnd, and SubagentStop hooks against pi's lifecycle events, then drives the same Python scripts (`session_start_hook.py`, `session_stop_hook.py`, `subagent_stop_hook.py`) the other runtimes use. pi transcripts are read from `~/.pi/`, `<cwd>/.pi/`, or `~/.pi/agent/sessions/`.

## Install

Install it globally for pi (recommended helper):

```bash
./scripts/install-pi-extension
```

For live development (so extension updates track this repo automatically):

```bash
./scripts/install-pi-extension --symlink
```

Manual install (without helper):

```bash
mkdir -p ~/.pi/agent/extensions/lib
cp extensions/pi/parsidion/parsidion.ts ~/.pi/agent/extensions/parsidion.ts
cp extensions/pi/parsidion/parsidion.md ~/.pi/agent/extensions/parsidion.md
cp extensions/pi/parsidion/lib/parsidion-status.ts ~/.pi/agent/extensions/lib/parsidion-status.ts
```

Then in pi:

```text
/reload
/parsidion
```

`/parsidion` shows:

- resolved script directory
- transcript/session details
- effective Anthropic / GLM config status

## omp (oh my pi)

omp is an extension-only runtime like pi and reuses this same TypeScript extension source, installed into omp's own extensions directory instead:

```bash
uv run install.py connect omp
uv run install.py disconnect omp
```

The extension lands in `$PI_CONFIG_DIR/agent/extensions` (default `~/.omp/agent/extensions`; override with `install.py connect omp --omp-home <dir>`). omp's extension loader resolves the extension's `@mariozechner/*` imports and emits every lifecycle event the extension binds (`session_start`, `before_agent_start`, `session_before_compact`, `session_compact`, `turn_end`, `session_shutdown`). omp's task tool emits no `subagent:result` custom messages, so subagent-transcript capture is a graceful no-op there. Verified against omp 17.3.8 (0.19.0 release notes).

## Effective Anthropic / GLM Configuration

For Anthropic / GLM-compatible settings, status precedence is:

1. real environment variable
2. `<resolved vault>/config.yaml` `anthropic_env`
3. unset

Secret values such as `ANTHROPIC_AUTH_TOKEN` are masked in status output. Python hook scripts remain authoritative for runtime behavior; the pi extension only reports effective status.

## Smoke Tests

From the `parsidion` repo root, run these to validate the pi hook wiring:

```bash
# 1) SessionEnd hook against a pi transcript path
TEST_TXT="$HOME/.pi/agent/sessions/--tmp--/pi-session-test.jsonl"
mkdir -p "$(dirname "$TEST_TXT")"
printf '%s\n' \
  '{"type":"message","message":{"role":"assistant","content":[{"type":"text","text":"Root cause was a missing lock."}]}}' \
  > "$TEST_TXT"
TMP_VAULT=$(mktemp -d)
printf '%s' "{\"cwd\":\"$PWD\",\"transcript_path\":\"$TEST_TXT\"}" \
  | CLAUDE_VAULT="$TMP_VAULT" uv run --no-project skills/parsidion/scripts/session_stop_hook.py

# 2) SubagentStop hook against a pi subagent transcript path
printf '%s' "{\"cwd\":\"$PWD\",\"agent_transcript_path\":\"$TEST_TXT\",\"agent_id\":\"pi-smoke-1\",\"agent_type\":\"Explore\"}" \
  | CLAUDE_VAULT="$TMP_VAULT" uv run --no-project skills/parsidion/scripts/subagent_stop_hook.py

# 3) Summarizer dry-run on explicit pi transcript entry
SESSIONS_FILE=$(mktemp)
printf '%s\n' "{\"session_id\":\"pi-smoke-summarizer\",\"transcript_path\":\"$TEST_TXT\",\"project\":\"mypi\",\"categories\":[\"error_fix\"],\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"source\":\"subagent\",\"agent_type\":\"Explore\"}" > "$SESSIONS_FILE"
env -u CLAUDECODE uv run --no-project skills/parsidion/scripts/summarize_sessions.py \
  --sessions "$SESSIONS_FILE" --vault "$TMP_VAULT" --dry-run

# cleanup
rm -f "$TEST_TXT" "$SESSIONS_FILE"
rm -rf "$TMP_VAULT"
```

Expected behavior:

- hooks return `{}` and do not reject pi transcript paths
- `pending_summaries.jsonl` is created in `TMP_VAULT` when categories are significant
- summarizer prints a `[dry-run] Would write:` note path (proves transcript parsing worked)

## Troubleshooting

If script discovery fails, set one of:

```bash
export PARSIDION_SCRIPTS_DIR="$HOME/Repos/parsidion/skills/parsidion/scripts"
# or
export PARSIDION_DIR="$HOME/Repos/parsidion"
```

Script resolution order is `PARSIDION_SCRIPTS_DIR`, then
`PARSIDION_DIR/skills/parsidion/scripts`, then the installed
`~/.claude/skills/parsidion/scripts` — nothing else. For repo-local
development (running hooks from a parsidion checkout), export
`PARSIDION_SCRIPTS_DIR`; the extension deliberately does not search
cwd-relative sibling directories, so cloning an untrusted repository named
`parsidion` beside a workspace cannot inject hook scripts into pi/omp
sessions (SEC-003). Hook subprocesses also receive only an allowlisted
environment (`PATH`, `HOME`, `CLAUDE_VAULT`, `PARSIDION_*`, `LC_*`/`XDG_*`,
and the Anthropic/proxy variables) rather than the full `process.env`.

Then in pi:

```text
/reload
/parsidion
```

## Related Documentation

- [AGENT-ADAPTERS.md](AGENT-ADAPTERS.md) — The runtime-adapter registry that drives `connect`/`disconnect` for every supported runtime (claude/codex/gemini/pi + external drop-ins)
- [ARCHITECTURE.md](ARCHITECTURE.md) — Hook scripts, transcript compatibility, and accepted roots
- [README.md](../README.md) — Project overview and installation
