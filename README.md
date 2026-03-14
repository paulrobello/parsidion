# Parsidion CC

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)

A second brain for Claude Code -- Obsidian-backed knowledge management that gives Claude persistent memory, cross-session context, and a searchable vault of everything it learns.

Parsidion CC replaces Claude Code's built-in auto memory with a richly organized Obsidian vault. Session lifecycle hooks automatically capture learnings, load relevant context at startup, and snapshot working state before compaction. A research agent saves structured findings, and an AI-powered summarizer generates vault notes from session transcripts.

![Parsidion CC Architecture](https://raw.githubusercontent.com/paulrobello/parsidion-cc/main/parsidion-cc-architecture.png)

> [View the interactive architecture slideshow](https://paulrobello.github.io/parsidion-cc/vault-architecture-slideshow.html) for a detailed walkthrough of every component.
>
> **Build session slideshows:** [Vault Explorer Agent](https://paulrobello.github.io/parsidion-cc/vault-explorer-slideshow.html) · [Research Documentation Agent](https://paulrobello.github.io/parsidion-cc/research-agent-slideshow.html)

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Components](#components)
- [Configuration](#configuration)
- [Vault Git Integration](#vault-git-integration)
- [File Locations](#file-locations)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)
- [Related Documentation](#related-documentation)

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** -- Python package runner and manager
- **[Obsidian](https://obsidian.md/)** (optional) -- for vault browsing and graph view
- **Claude Code** -- the CLI this toolkit extends
- **[mcpl](https://github.com/kenneth-liao/mcp-launchpad)** (optional) -- MCP Launchpad, a unified CLI for discovering and calling tools from any MCP server; used by the research agent as a fallback search gateway (see [docs/MCPL.md](docs/MCPL.md))

## Quick Start

1. **Clone the repository:**
   ```bash
   git clone https://github.com/paulrobello/parsidion-cc.git
   cd parsidion-cc
   ```

2. **Run the installer:**
   ```bash
   uv run install.py
   ```
   This installs the vault skill, research agent, session hooks, and always-on vault guidance into `~/.claude/`.

3. **Restart Claude Code** to activate the hooks.

That's it. Claude Code now has persistent memory backed by an Obsidian vault at `~/ClaudeVault/`.

## Installation

```bash
# Interactive install (prompts for vault location)
uv run install.py

# Non-interactive with default vault (~/ClaudeVault)
uv run install.py --force --yes

# Non-interactive with custom vault path
uv run install.py --vault ~/MyVault --yes

# Preview without making changes
uv run install.py --dry-run
```

**Options:**

| Flag | Description |
|------|-------------|
| `--vault PATH` | Obsidian vault path (skips interactive prompt) |
| `--claude-dir PATH` | Target Claude config dir (default: `~/.claude`) |
| `--dry-run / -n` | Preview all actions, no changes made |
| `--verbose / -v` | Show detailed output |
| `--force / -f` | Overwrite existing skill files without prompting |
| `--yes / -y` | Skip all confirmation prompts; uses `~/ClaudeVault` if `--vault` not given |
| `--skip-hooks` | Do not modify `settings.json` |
| `--skip-agent` | Do not install the research agent |
| `--uninstall` | Remove installed skill, agent, and hook registrations |

After installation, open the vault path in Obsidian and restart Claude Code to activate hooks.

## Components

### Claude Vault (`~/.claude/skills/claude-vault/`)

An Obsidian vault-based knowledge management system that replaces Claude Code's built-in auto memory with a richly organized, searchable, cross-linked knowledge base at `~/ClaudeVault/`.

**Auto-triggering:** The skill includes YAML frontmatter with a description that enables automatic invocation when users mention saving knowledge, checking notes, or persisting findings across sessions.

**Scripts:**

| Script | Purpose |
|--------|---------|
| `vault_common.py` | Shared library (frontmatter parsing, search, path utilities, config loader, git commit) |
| `session_start_hook.py` | SessionStart hook - loads project-relevant vault context; `--ai [MODEL]` enables AI-powered note selection via `claude -p`; `--debug` logs injected context to `$TMPDIR` |
| `session_stop_hook.py` | SessionEnd hook - queues sessions to `pending_summaries.jsonl` (deduped by session_id, `fcntl`-locked); auto-launches summarizer in background |
| `summarize_sessions.py` | On-demand AI summarizer - generates structured vault notes from queued sessions (PEP 723, uses `claude-agent-sdk`) |
| `pre_compact_hook.py` | PreCompact hook - snapshots working state before compaction |
| `update_index.py` | Rebuilds `~/ClaudeVault/CLAUDE.md` index (includes `## Existing Tags` for summarizer, vault health from `doctor_state.json`) |
| `vault_doctor.py` | Scans vault notes for structural issues (missing frontmatter, broken wikilinks, orphan notes, etc.); repairs via Claude haiku; tracks state in `doctor_state.json` |
| `check_graph_coverage.py` | Audits vault tags vs graph.json color groups; shows uncovered tags and stale entries |
| `run_trigger_eval.py` | Trigger accuracy eval (skill-selection simulation) |
| `run_trigger_eval.sh` | Shell wrapper for running eval from a separate terminal (macOS/Linux) |
| `run_trigger_eval.bat` | Batch wrapper for running eval from a separate terminal (Windows) |
| `migrate_research.py` | One-time migration from `~/Repos/research/` |
| `migrate_memory.py` | One-time migration from `~/.claude/memory/` |

**Templates:** 8 note templates (daily, project, language, framework, pattern, debugging, tool, research)

**Vault structure:**
```
~/ClaudeVault/
  CLAUDE.md                  # Auto-generated index (includes tag cloud + Existing Tags list)
  config.yaml                # Optional -- hook/summarizer settings (see Configuration)
  pending_summaries.jsonl    # Queue of sessions awaiting AI summarization
  Daily/YYYY-MM/DD.md        # Session summaries (e.g. Daily/2026-03/13.md)
  Projects/                  # Per-project context
  Languages/                 # Language-specific knowledge
  Frameworks/                # Framework knowledge
  Patterns/                  # Design patterns, solutions
  Debugging/                 # Error patterns, fixes
  Tools/                     # CLI tools, packages
  Research/                  # Deep-dive research
  History/                   # Historical notes
  Templates/                 # Symlink to skill templates
```

**Graph view color groups** (configured in `~/ClaudeVault/.obsidian/graph.json`, first-match-wins):

| Priority | Category | Color | RGB (decimal) | Tags |
|----------|----------|-------|---------------|------|
| 1 | Projects | Cyan `#00BCD4` | 48340 | synknot, fractal-flythroughs, parvitar, parsistant, termflix, parvault, cctmux, parsidion-cc |
| 2 | Debugging | Red/Orange `#FF5722` | 16733986 | debugging |
| 3 | Patterns | Green `#4CAF50` | 5025616 | memory, migration, sync |
| 4 | Research | Purple `#9C27B0` | 10233776 | research, e2b, qdrant, pkm-apps-comparison |
| 5 | Tools & SDKs | Blue `#2196F3` | 2201331 | claude-code, claude-agent-sdk, claude, rich, mcp, ollama, maturin, redis, websockets, sentry, mermaid-cli, custom-tools, acp-protocol, tool, api, encryption |
| 6 | Languages | Amber `#FFC107` | 16761095 | rust, python, swift, swiftui, typescript, nextjs, react, macos, macos-26, rust-packages |
| 7 | Terminal | Teal `#009688` | 38536 | terminal, par-term, par-term-emu-core-rust |
| 8 | Graphics / 3D | Pink `#E91E63` | 15277667 | wgpu, sdf, sdf-terrain, voxel, fractals, mandel, vrm, avatar, face-tracking |

The claude-vault skill includes a sub-workflow for updating these groups -- add tags to existing groups or create new ones when new projects or topics are introduced. RGB colors are stored as decimal integers (e.g., `int("FF5722", 16)` -> `16733986`).

### CLAUDE-VAULT.md (`~/.claude/CLAUDE-VAULT.md`)

An always-on guidance file loaded every Claude Code session via `@CLAUDE-VAULT.md` in `~/.claude/CLAUDE.md`. It enforces the **vault-first rule** unconditionally — no explicit invocation needed.

**What it enforces:**
- **Debugging:** Search `~/ClaudeVault/Debugging/` before diagnosing any error. Extract the key signal (exception class, package name, distinctive phrase) and Grep the vault first. If found, apply the documented fix. If not, diagnose then save the solution.
- **Implementation:** Search `~/ClaudeVault/Patterns/`, `Frameworks/`, `Languages/`, and `Projects/` before writing non-trivial code. Reuse proven implementations from prior projects rather than writing from scratch.
- **Saving solutions:** After solving a non-obvious problem, save it to the appropriate vault folder and rebuild the index.

The installer copies `CLAUDE-VAULT.md` from the repo root to `~/.claude/` and ensures the `@CLAUDE-VAULT.md` import line exists in `~/.claude/CLAUDE.md`. Uninstall removes both.

### Research Agent (`~/.claude/agents/research-documentation-agent.md`)

Technical research agent that searches the vault first, conducts web research, and saves findings to the appropriate vault folder with proper YAML frontmatter. Fetches pages via `agentchrome page html` piped through `scripts/html-to-md` for noise-free markdown (curl fallback if agentchrome unavailable). Uses `mcpl` as a fallback search gateway when Brave Search hits rate limits — see [docs/MCPL.md](docs/MCPL.md) for mcpl setup.

### HTML to Markdown (`scripts/html-to-md`)

A PEP 723 standalone script (installed to `~/.claude/scripts/html-to-md`) that converts HTML to clean, noise-free markdown optimized for LLM consumption. Strips navigation, banners, cookie notices, and script/style noise while preserving code fences with language annotations. Used by the research agent to clean `agentchrome` page output.

```bash
uv run --script scripts/html-to-md page.html          # file → stdout
uv run --script scripts/html-to-md - < page.html      # stdin → stdout
agentchrome page html | uv run --script ~/.claude/scripts/html-to-md - --url https://example.com
```

### Context Preview (`scripts/show-context`)

A shell script that previews what vault context would be injected at session start for a given project directory. Useful for debugging the SessionStart hook. Requires `jq` to be installed.

```bash
./scripts/show-context                    # Preview context for cwd
./scripts/show-context ~/Repos/myproject  # Preview context for a specific project
```

### Hooks (`~/.claude/settings.json`)

All hooks read `~/ClaudeVault/config.yaml` for settings (see [Configuration](#configuration)).

| Hook Event | Script | Timeout | Config section | Notes |
|------------|--------|---------|----------------|-------|
| SessionStart | `session_start_hook.py` | 10 s (30 s with `--ai`) | `session_start_hook` | `--ai [MODEL]` or `session_start_hook.ai_model` enables AI selection |
| SessionEnd | `session_stop_hook.py` | 10 s (30 s with `--ai`) | `session_stop_hook` | Auto-launches summarizer when pending entries exist |
| PreCompact | `pre_compact_hook.py` | 10 s | `pre_compact_hook` | Configurable transcript lines |

## Configuration

All hooks and the summarizer read `~/ClaudeVault/config.yaml`. Precedence: **defaults -> config.yaml -> CLI args** (last one wins).

Copy the template to get started:
```bash
cp ~/.claude/skills/claude-vault/templates/config.yaml ~/ClaudeVault/config.yaml
```

```yaml
session_start_hook:
  ai_model: null           # Model for AI note selection (null = disabled)
  max_chars: 4000          # Max context injection characters
  ai_timeout: 25           # AI call timeout in seconds
  recent_days: 3           # Days to look back for recent notes
  debug: false             # Append injected context to debug log in $TMPDIR

session_stop_hook:
  ai_model: null           # Model for AI classification (null = disabled)
  ai_timeout: 25           # AI call timeout in seconds
  auto_summarize: true     # Auto-launch summarizer when pending entries exist

pre_compact_hook:
  lines: 200               # Transcript lines to analyse

summarizer:
  model: claude-sonnet-4-6
  max_parallel: 5          # Concurrent summarization tasks
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false           # SDK session persistence (for debugging)

git:
  auto_commit: true        # Auto-commit vault changes after writes
```

## Vault Git Integration

The vault supports optional git version control. When `~/ClaudeVault/.git` exists, scripts automatically stage and commit changes after every write (daily notes, index rebuilds, session notes). Controlled by `git.auto_commit` in config.

```bash
cd ~/ClaudeVault
git init
echo ".obsidian/" > .gitignore
git add -A && git commit -m "chore(vault): initial commit"
```

If no `.git` directory is present, all git operations are silent no-ops.

## File Locations

```
~/.claude/
  CLAUDE.md                          # Global Claude Code instructions (@imports CLAUDE-VAULT.md)
  CLAUDE-VAULT.md                    # Always-on vault-first guidance (installed by parsidion-cc)
  settings.json                      # Hooks, permissions, plugins
  agents/
    research-documentation-agent.md  # Research agent (vault-integrated)
  scripts/
    html-to-md                       # HTML → clean markdown (used by research agent)
  skills/claude-vault/
    SKILL.md                         # Vault skill definition
    scripts/                         # Hook scripts and utilities
    templates/                       # Note templates + config.yaml reference

~/ClaudeVault/                       # Obsidian vault (knowledge base)
  config.yaml                        # Optional hook/summarizer settings
```

## Usage

**Rebuild vault index:**
```bash
uv run --no-project ~/.claude/skills/claude-vault/scripts/update_index.py
```

**Summarize queued sessions** (generates structured vault notes via Claude Agent SDK):
```bash
# Process all pending sessions (run from a terminal, not inside Claude Code)
uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py

# If running from inside a Claude Code session, unset CLAUDECODE to allow nesting:
env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py

# Preview without writing
uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py --dry-run

# Process an explicit file (e.g. to test a single entry)
uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py --sessions /path/to/file.jsonl
```

**Run vault doctor** (scan for issues and repair via Claude haiku):
```bash
# Scan and report only
uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --dry-run

# Repair repairable issues (must unset CLAUDECODE to allow nested claude calls)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --fix --limit 20

# Errors only; skip warnings
uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --errors-only --dry-run

# Ignore state file, rescan everything
uv run --no-project ~/.claude/skills/claude-vault/scripts/vault_doctor.py --no-state --dry-run
```

The doctor writes `~/ClaudeVault/doctor_state.json` to track processed notes. Notes that time out twice are flagged `needs_review` and skipped on future runs. The vault health summary appears in `CLAUDE.md` after running `update_index.py`.

**Run trigger eval** (from a separate terminal, not inside Claude Code):
```bash
bash ~/.claude/skills/claude-vault/scripts/run_trigger_eval.sh
```

**Preview session start context:**
```bash
./scripts/show-context
./scripts/show-context /path/to/project
```

**Search vault programmatically:**
```python
import sys
sys.path.insert(0, str(Path.home() / ".claude/skills/claude-vault/scripts"))
from vault_common import find_notes_by_tag, find_notes_by_project
```

**Audit graph color group coverage** (find uncovered vault tags, spot stale group entries):
```bash
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py

# Only show tags used 2+ times
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py --threshold 2

# JSON output for scripting
python ~/.claude/skills/claude-vault/scripts/check_graph_coverage.py --json
```

**Reinstall after source changes:**
```bash
uv run install.py --force --yes
```

**Uninstall:**
```bash
uv run install.py --uninstall
```

## Troubleshooting

### Hooks not firing

- Verify hooks are registered in `~/.claude/settings.json`. Look for `hooks` entries pointing to the hook scripts.
- Re-run `uv run install.py --force --yes` to re-register hooks.
- Check that the script paths in `settings.json` are correct and the files exist at those paths.
- Restart Claude Code after any settings.json change.

### Vault not created

- The vault directory (`~/ClaudeVault/` by default) is created automatically by the SessionStart hook on first run.
- If it was not created, check that the hook is firing (see above).
- You can create it manually: `mkdir -p ~/ClaudeVault` and then run the installer.

### `uv` not found

- Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Ensure `uv` is on your PATH. Restart your shell after installation.
- Verify with `uv --version`.

### Timeout errors with `--ai` flag

- The `--ai` flag on session start/stop hooks requires a longer timeout (30 seconds) because it calls `claude -p` for AI-powered note selection.
- Update `settings.json` to set the hook timeout to `30000` ms:
  ```json
  {
    "command": "uv run --no-project ~/.claude/skills/claude-vault/scripts/session_start_hook.py --ai",
    "timeout": 30000
  }
  ```
- If timeouts persist, increase `ai_timeout` in `~/ClaudeVault/config.yaml`.

### Summarizer fails to run

- The summarizer cannot run inside an active Claude Code session. Run from a separate terminal.
- If running from inside Claude Code, unset the guard variable: `env -u CLAUDECODE uv run --no-project ~/.claude/skills/claude-vault/scripts/summarize_sessions.py`
- Check that `pending_summaries.jsonl` exists and has entries.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding constraints, and PR guidelines.

## License

[MIT](LICENSE) -- [Paul Robello](https://github.com/paulrobello)

## Related Documentation

- [docs/MCPL.md](docs/MCPL.md) -- MCP Launchpad CLI: installation, configuration, and integration with Claude Code
- [docs/DOCUMENTATION_STYLE_GUIDE.md](docs/DOCUMENTATION_STYLE_GUIDE.md) -- Documentation standards for this project
