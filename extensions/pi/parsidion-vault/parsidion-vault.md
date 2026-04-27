# parsidion-vault pi extension

Global pi extension that bridges pi lifecycle events to the existing Parsidion Python hook scripts.

## Installation

From the `parsidion` repo root:

```bash
# Recommended helper
./scripts/install-pi-extension

# Dev mode: install as symlinks so edits in this repo are picked up immediately
./scripts/install-pi-extension --symlink
```

Manual install (without helper):

```bash
mkdir -p ~/.pi/agent/extensions
cp extensions/pi/parsidion-vault/parsidion-vault.ts ~/.pi/agent/extensions/parsidion-vault.ts
cp extensions/pi/parsidion-vault/parsidion-vault.md ~/.pi/agent/extensions/parsidion-vault.md
```

If the extension cannot find Parsidion scripts automatically, set one of:

```bash
# Option A: point directly at the scripts folder
export PARSIDION_SCRIPTS_DIR="$HOME/Repos/parsidion/skills/parsidion/scripts"

# Option B: point at the repo root (extension appends skills/parsidion/scripts)
export PARSIDION_DIR="$HOME/Repos/parsidion"
```

Then in pi:

```text
/reload
/parsidion-vault
```

You should see the resolved `scriptDir` in the status output.
The status output also includes an `Anthropic config:` section showing effective GLM/Anthropic-compatible configuration.

## What it does

It invokes these existing Python handlers directly:

- `session_start_hook.py`
- `session_stop_hook.py`
- `pre_compact_hook.py`
- `post_compact_hook.py`
- `subagent_stop_hook.py`

It does **not** reimplement their vault logic in TypeScript.

## How it works

Pi does not expose the same hook stdin/stdout contract as Claude Code, so this extension adapts pi to the existing Python scripts by:

1. mapping pi events to Parsidion hooks
2. invoking the Python scripts with the expected stdin payloads
3. passing native pi transcript paths when available (`~/.pi/...` or `<project>/.pi/...`)
4. falling back to synthetic JSONL transcripts only if a real session file is unavailable
5. inserting returned vault context into pi as a visible custom message

## Event mapping

- `session_start` -> `session_start_hook.py`
- `session_before_compact` -> `pre_compact_hook.py`
- `session_compact` -> `post_compact_hook.py`
- `turn_end` -> scan for newly finished subagents and invoke `subagent_stop_hook.py` (detached)
- `session_shutdown` -> final subagent scan + `session_stop_hook.py` (detached)

## What you will see in pi

When the vault injects context, the extension inserts a visible message in the session:

- message type: `parsidion-vault:context`
- title in UI: `Vault context injected`

That message is also what gets inserted into the model context, so what you see is what the model receives.

## Script resolution

The extension looks for Parsidion scripts in this order:

1. `PARSIDION_SCRIPTS_DIR`
2. `PARSIDION_DIR/skills/parsidion/scripts`
3. `../parsidion/skills/parsidion/scripts`
4. `../parsidion/scripts`
5. `~/.claude/skills/parsidion/scripts`

## Commands

### `/parsidion-vault`

Shows current integration status:

- resolved script directory
- synthetic transcript path
- queued context chunks
- processed subagent count
- current session file
- effective Anthropic / GLM config status

Anthropic / GLM status reports these keys when present:

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_AUTH_TOKEN`
- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_CUSTOM_HEADERS`
- `API_TIMEOUT_MS`
- `ANTHROPIC_DEFAULT_HAIKU_MODEL`
- `ANTHROPIC_DEFAULT_SONNET_MODEL`
- `ANTHROPIC_DEFAULT_OPUS_MODEL`

Precedence per key:

1. real environment variable
2. `~/ClaudeVault/config.yaml` → `anthropic_env`
3. unset

Secret values such as `ANTHROPIC_AUTH_TOKEN` are masked in status output.
The `/parsidion-vault` command is read-only status reporting; Python hook scripts remain the runtime source of truth.

## Notes

- `session_stop_hook.py` and `subagent_stop_hook.py` are launched detached so they do not block pi shutdown or the parent session.
- Real pi transcript paths are preferred (`~/.pi/agent/sessions/...` and project-local `.pi/agent-sessions/...`).
- Synthetic transcripts under `~/.claude/pi-vault-hooks/` are only used as a fallback when no session file path is available.
- Subagent support depends on the installed pi `subagent` extension, which emits visible `subagent:result` messages containing the subagent session file.

## Reload

After editing or installing, run:

```text
/reload
```
