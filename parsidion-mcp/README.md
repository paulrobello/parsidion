# parsidion-mcp

Optional Model Context Protocol (MCP) server that exposes a Parsidion vault to **Claude Desktop** and any other MCP-capable client over stdio. Lives in this subdirectory and is installed independently from the main skill.

## Overview

`parsidion-mcp` is a FastMCP-based server that bridges Parsidion vault operations — semantic search, note read/write, context-block generation, index rebuilds, vault-doctor scans, and parsight code-memory search — to MCP clients. It imports `vault_common` / `vault_search` from the parent repo via the editable `.[tools]` install, so it always reflects the same vault code path the hooks use.

For the full tool reference, configuration, and Claude Desktop setup, see [../docs/MCP.md](../docs/MCP.md).

## Install

```bash
cd parsidion-mcp
uv tool install --editable .
```

`uv tool install` places the `parsidion-mcp` binary in `~/.local/bin/` (or the equivalent `uv` tool bin directory on your platform).

> **Note:** On the first `vault_search` call with a query, `fastembed` downloads the configured ONNX embedding model (~67 MB) and caches it. Subsequent calls are fast. If the embeddings database does not yet exist, the tool returns a clear error prompting you to run `rebuild_index` first.

## Configure Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS; equivalent path on other platforms):

```json
{
  "mcpServers": {
    "parsidion": {
      "command": "/Users/<username>/.local/bin/parsidion-mcp"
    }
  }
}
```

Replace `<username>` with your actual username. Use the absolute path returned by `which parsidion-mcp` — Claude Desktop launches processes with a minimal `PATH` that may not include `~/.local/bin/`.

## Tools

Eight tools are exposed. Full request/response shapes live in [../docs/MCP.md](../docs/MCP.md):

| Tool | Description |
|------|-------------|
| `vault_search` | Semantic search (natural language query) or metadata search (tag/folder/type/project/days) |
| `vault_read` | Read a vault note by relative or absolute path |
| `vault_write` | Create or overwrite a vault note |
| `vault_context` | Return a session-start-style context block (compact index or verbose summaries) |
| `rebuild_index` | Rebuild `CLAUDE.md`, `MANIFEST.md` files, and the `note_index` SQLite table |
| `vault_doctor` | Scan vault notes for structural issues; optionally repair them; `--fix-sessions` detects multi-note sessions |
| `vault_health` | Composite 0–100 vault-health score across seven dimensions (index freshness, queue, graph, metadata, embeddings, tags, files) with concrete next-action commands; subprocess wrapper around `vault-stats --health --json` (ENH-007) |
| `code_search` | Search a parsight-indexed repository's code graph by natural language; requires the [parsight](../docs/PARSIGHT.md) backend (returns a clear error if parsight is unavailable) |

## Development

```bash
cd parsidion-mcp
make checkall    # ruff + pyright + pytest
```

The subproject has its own `Makefile`, `pyproject.toml`, `src/`, and `tests/`. It is part of the parent repo's quality gate via `make checkall-mcp` from the repo root.

## Related Documentation

- [../docs/MCP.md](../docs/MCP.md) — full tools reference and configuration
- [../docs/PARSIGHT.md](../docs/PARSIGHT.md) — parsight code-memory backend
- [../README.md](../README.md) — main project README
