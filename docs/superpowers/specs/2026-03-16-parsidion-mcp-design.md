# parsidion-mcp Design Spec

This document specifies the architecture and implementation of `parsidion-mcp`, a FastMCP-based MCP server that exposes the Parsidion vault knowledge management system to Claude Desktop and other MCP-capable clients.

## Table of Contents

- [Overview](#overview)
- [Deployment Model](#deployment-model)
- [Package Structure](#package-structure)
- [Tools](#tools)
  - [vault_search](#vault_search)
  - [vault_read](#vault_read)
  - [vault_write](#vault_write)
  - [vault_context](#vault_context)
  - [rebuild_index](#rebuild_index)
  - [vault_doctor](#vault_doctor)
- [Architecture](#architecture)
- [Error Handling](#error-handling)
- [Testing](#testing)
- [Installation](#installation)
- [Claude Desktop Configuration](#claude-desktop-configuration)
- [Related Documentation](#related-documentation)

## Overview

**Date:** 2026-03-16
**Status:** Implemented

`parsidion-mcp` is a FastMCP-based MCP server that exposes the Parsidion vault knowledge management system to Claude Desktop (and any other MCP-capable client). It lives at `parsidion/parsidion-mcp/` as an independent Python package.

## Deployment Model

This is a **local-only deployment**. Both `parsidion-mcp` and its `parsidion[search]` dependency must be installed as editable packages (`uv tool install --editable`). Non-editable installs are not supported due to the `py-modules` layout of `parsidion` (see `pyproject.toml` comment). All installation instructions assume local development use.

## Package Structure

```
parsidion/
└── parsidion-mcp/
    ├── pyproject.toml
    └── src/
        └── parsidion_mcp/
            ├── __init__.py
            ├── server.py        # FastMCP app + entry point
            └── tools/
                ├── __init__.py
                ├── search.py    # vault_search tool
                ├── notes.py     # vault_read, vault_write
                ├── context.py   # vault_context
                └── ops.py       # rebuild_index, vault_doctor
```

### Dependencies (`pyproject.toml`)

```toml
[project]
name = "parsidion-mcp"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=2.0",
    "parsidion[search]",
]

[tool.uv.sources]
parsidion = { path = "../", editable = true }

[project.scripts]
parsidion-mcp = "parsidion_mcp.server:main"

[dependency-groups]
dev = [
    "pytest>=9.0",
    "pytest-timeout>=0.5",
    "pytest-cov>=4.0",
    "ruff>=0.9",
    "pyright>=1.1",
]
```

The `parsidion[search]` dependency brings in `vault_common`, `vault_search`, `fastembed`, and `sqlite-vec`. **First-run note:** on the first `vault_search` call, `fastembed` downloads the `BAAI/bge-small-en-v1.5` ONNX model (~67 MB) and caches it. This can take 30-60 seconds. Subsequent calls are fast. The tool returns an informative message if this initial download is still in progress.

## Tools

### `vault_search`

Searches vault notes using semantic (vector) or metadata (filter) mode.

**Parameters:**
- `query: str | None` - natural language query (enables semantic mode)
- `tag: str | None` - filter by tag
- `folder: str | None` - filter by folder name
- `note_type: str | None` - filter by note type
- `project: str | None` - filter by project
- `recent_days: int | None` - only notes modified within N days
- `top_k: int = 10` - max results
- `min_score: float = 0.45` - minimum cosine similarity (semantic mode only)

**Behavior:** Semantic mode when `query` is provided (calls `vault_search.search()`); metadata mode otherwise (calls `vault_search.query()`). Returns JSON array of all fields returned by the underlying functions: `score`, `stem`, `title`, `folder`, `tags`, `path`, `summary`, `note_type`, `project`, `confidence`, `mtime`, `related`, `is_stale`, `incoming_links`. Score is `null` for metadata results.

**Missing embeddings DB:** The underlying `vault_search.search()` returns an empty list silently when the DB is absent. The tool adds a pre-check: if `query` is provided and `embeddings.db` does not exist, return `ERROR: embeddings DB not found - run rebuild_index first` rather than an empty result.

### `vault_read`

Reads a vault note by path.

**Parameters:**
- `path: str` - path relative to vault root (e.g. `Patterns/my-note.md`) or absolute

**Returns:** Full note content (frontmatter + body) as a string.

**Safety:** Resolves path against `vault_common.VAULT_ROOT`; rejects any path that escapes the vault root with `ERROR: path escapes vault root`.

### `vault_write`

Creates or overwrites a vault note.

**Parameters:**
- `path: str` - path relative to vault root
- `content: str` - full note content including YAML frontmatter

**Frontmatter validation:** Not performed. The tool trusts the caller to provide valid frontmatter per vault conventions. Invalid or missing frontmatter will be caught by `vault_doctor` on the next scan.

**Returns:** Success confirmation with resolved absolute path.

**Safety:** Same path containment check as `vault_read`. Parent directories are created automatically (`mkdir -p` equivalent).

### `vault_context`

Returns vault context in the same format as the session start hook - compact one-line-per-note index by default, full summaries when `verbose=True`.

**Parameters:**
- `project: str | None` - project name to filter context for
- `recent_days: int = 3` - recency window for recent notes
- `verbose: bool = False` - full note summaries vs compact index

**Note selection algorithm** (implemented directly in `context.py` using `vault_common`):
1. If `project` is set: call `vault_common.find_notes_by_project(project)` to get project notes
2. Call `vault_common.find_recent_notes(recent_days)` to get recent notes
3. Merge, deduplicating by path (project notes first)
4. If `verbose=False`: produce a compact index using `vault_common.build_compact_index(notes)` - one line per note: `- [[stem]] Title (folder) - \`tag1\` \`tag2\``; header: `**Available vault notes** (...)`
5. If `verbose=True`: call `vault_common.build_context_block(notes)` for full summaries

**Returns:** Context string ready for injection into a system prompt.

### `rebuild_index`

Rebuilds the vault index (`CLAUDE.md`, per-folder `MANIFEST.md` files, and `note_index` table in `embeddings.db`).

**Parameters:** None.

**Returns:** Combined stdout + stderr from `update_index.py`. Timeout: 30s.

### `vault_doctor`

Scans vault notes for structural issues; optionally repairs them via Claude haiku.

**Parameters:**
- `fix: bool = False` - `False` to scan and report only (omit `--fix` flag); `True` to repair via Claude haiku (pass `--fix` flag)
- `errors_only: bool = False` - only report errors, skip warnings (pass `--errors-only` flag when `True`)
- `limit: int | None = None` - max notes to repair; only relevant when `fix=True` (pass `--limit N` when set)

**Unexposed flags:** `--dry-run`, `--model`, `--no-state`, `--jobs`, `--timeout` are not exposed. The default of 3 parallel workers and 120s per-repair timeout are used.

**Returns:** Combined stdout + stderr from `vault_doctor.py`. Timeout: 120s.

## Architecture

### Importing vault_common / vault_search

The `parsidion[search]` editable path dependency makes `vault_common` and `vault_search` directly importable - no `sys.path` manipulation needed in the MCP server.

### Finding scripts for subprocess calls

```python
import vault_common

# TEMPLATES_DIR is always ~/.claude/skills/parsidion/templates/
# (patched by the installer but its parent is always the skill root).
# SCRIPTS_DIR is a stable structural invariant: TEMPLATES_DIR.parent / "scripts"
# == ~/.claude/skills/parsidion/scripts/
SCRIPTS_DIR = vault_common.TEMPLATES_DIR.parent / "scripts"
```

This invariant holds because the installer always sets `TEMPLATES_DIR` to `<skill_root>/templates/`. If a custom vault path is configured, only `VAULT_ROOT` is patched - `TEMPLATES_DIR` always points into `~/.claude/`. The MCP server documents this dependency explicitly with the comment above.

### Subprocess calls

`rebuild_index` and `vault_doctor` invoke the existing scripts via subprocess:

```python
subprocess.run(
    ["uv", "run", "--no-project", str(SCRIPTS_DIR / "update_index.py")],
    capture_output=True, text=True, timeout=30,
)
```

```python
args = [str(SCRIPTS_DIR / "vault_doctor.py")]
if fix:
    args.append("--fix")
if errors_only:
    args.append("--errors-only")
if limit is not None:
    args.extend(["--limit", str(limit)])

subprocess.run(
    ["uv", "run", "--no-project", *args],
    capture_output=True, text=True, timeout=120,
)
```

No `env -u CLAUDECODE` needed - Claude Desktop does not set that environment variable.

### Server entry point

```python
from fastmcp import FastMCP
from parsidion_mcp.tools import search, notes, context, ops

mcp = FastMCP("parsidion-mcp")

# Tool registrations (each module registers its tools on import)

def main() -> None:
    mcp.run()
```

Transport: stdio (FastMCP default, required by Claude Desktop).

## Error Handling

Every tool returns a plain string. On failure:
```
ERROR: <message>
```
On success: the result content.

| Condition | Error message |
|---|---|
| Subprocess non-zero exit | stderr from the subprocess |
| Path escapes vault root | `ERROR: path escapes vault root` |
| Vault root not found | `ERROR: vault root not found at <path>` |
| Embeddings DB missing (semantic search) | `ERROR: embeddings DB not found - run rebuild_index first` |
| Subprocess timeout | `ERROR: command timed out after <N>s` |

No exceptions propagate to the MCP transport layer.

## Testing

Location: `parsidion-mcp/tests/`

- **Unit tests** - each tool module tested with mocked `vault_common`, `vault_search`, and `subprocess.run`
- **Subprocess tests** - `rebuild_index` and `vault_doctor` tested with `unittest.mock.patch("subprocess.run")`, verifying correct flag construction for each parameter combination
- **Path safety tests** - path escape attempts in `vault_read` / `vault_write` return the expected error string
- **Integration smoke test** - reads one real note; skipped via `pytest.mark.skipif` when vault is absent (`not vault_common.VAULT_ROOT.exists()`)

Tools: `pytest`, `pytest-timeout`, `pytest-cov`, `ruff`, `pyright`.

## Installation

Both packages must be editable installs:

```bash
# 1. Ensure parsidion[search] is installed editably (may already be done)
cd parsidion/
uv tool install --editable ".[tools]"

# 2. Install the MCP server
cd parsidion-mcp/
uv tool install --editable .
```

`uv tool install` places the `parsidion-mcp` binary in `~/.local/bin/` (or equivalent uv tool bin dir). Verify:
```bash
which parsidion-mcp   # should print ~/.local/bin/parsidion-mcp
```

## Claude Desktop Configuration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "parsidion": {
      "command": "/Users/<username>/.local/bin/parsidion-mcp"
    }
  }
}
```

Use the full absolute path (output of `which parsidion-mcp`) rather than a bare command name. Claude Desktop launches processes with a minimal PATH that may not include `~/.local/bin/`, so the bare `parsidion-mcp` command may not resolve.

## Related Documentation

- [CLAUDE.md](../../CLAUDE.md) - Project instructions for parsidion
- [Vault Configuration](../../CLAUDE.md#vault-configuration) - Configuration options
- [vault_common.py](../../skills/parsidion/scripts/vault_common.py) - Core vault utilities
- [vault_search.py](../../skills/parsidion/scripts/vault_search.py) - Search implementation
