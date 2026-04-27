# Show HN: Parsidion – Persistent memory for Claude Code via a markdown vault

**URL:** https://github.com/paulrobello/parsidion

---

Claude Code forgets everything between sessions. Its built-in memory is a flat file that loses nuance fast. Parsidion replaces it with a structured markdown vault at `~/ClaudeVault/` -- plain markdown with YAML frontmatter, cross-linked with bidirectional wikilinks. Works with Obsidian, any text editor, or the included web viewer.

## How it works

Five stdlib-only Python hooks run at Claude Code lifecycle events:

- **SessionStart**: injects relevant vault notes as context. Optional haiku-powered selection.
- **SessionEnd**: detects learnable content in the transcript and queues it for summarization. Runs detached so it doesn't block exit.
- **PreCompact/PostCompact**: snapshots working state (task, files, git branch, dirty files) before context compaction, restores it after. Critical for long sessions.
- **SubagentStop**: captures subagent transcripts so research/exploration findings get harvested too.

A summarizer processes the queue via the Claude Agent SDK (up to 5 parallel sessions), with hierarchical chunking for long transcripts, embedding-based dedup, and a write-gate so Claude decides per-session if the content is worth persisting.

## Search

Global `vault-search` CLI with four modes: semantic (fastembed + sqlite-vec), metadata filtering (tag/folder/type/project/recency), full-text grep, and an interactive curses TUI. Claude uses it automatically via a vault-explorer subagent.

## Web-based vault viewer

Next.js app with two modes. **Read mode**: GFM rendering, clickable wikilinks, tag pills, inline editing with structured frontmatter editor, note creation/deletion. **Graph mode**: WebGL force-directed graph (Sigma.js + ForceAtlas2) showing 2-hop neighborhoods or full vault. HUD panel with similarity threshold, graph source toggle, node type filters, full physics controls, and live stats. Multi-tab browsing, file explorer sidebar, Cmd+K search. Graph data pre-computed from embeddings -- no live queries.

## CLI tools & MCP

`vault-stats` (analytics dashboard), `vault-new` (note scaffolding), `vault-review` (approve/reject pending sessions), `vault-export` (HTML/zip/PDF), `vault-merge` (AI dedup), `vault-doctor` (structural health + auto-repair). An MCP server exposes vault ops to Claude Desktop.

## Install

```bash
git clone https://github.com/paulrobello/parsidion.git && cd parsidion
uv run install.py
```

Restart Claude Code. Nightly auto-summarization: `uv run install.py --schedule-summarizer`.

## Technical choices

- **stdlib-only hooks** -- no pip deps in the critical path. Summarizer uses PEP 723 inline deps.
- **Plain markdown** -- SQLite only for embeddings/metadata cache; notes are always plain files.
- **Pre-computed graph** -- no runtime inference or server-side queries.
- **Optional git** -- auto-commits after writes if `~/ClaudeVault/.git` exists.

I've been running this across ~10 projects for a few weeks. The vault has 300+ notes and the difference is significant -- Claude picks up where it left off and stops re-inventing patterns it already learned.
