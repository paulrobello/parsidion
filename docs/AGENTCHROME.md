# AgentChrome

A fast native CLI that lets AI coding agents control a Chrome or Chromium browser via the Chrome DevTools Protocol, enabling real-time web interaction, page inspection, form automation, and screenshot capture without any Node.js or Python runtime.

## Table of Contents
- [Overview](#overview)
- [Why AgentChrome](#why-agentchrome)
- [Installation](#installation)
- [Key Capabilities](#key-capabilities)
- [Usage in Parsidion CC](#usage-in-parsidion-cc)
- [Common Commands](#common-commands)
- [Troubleshooting](#troubleshooting)
- [Related Documentation](#related-documentation)

## Overview

**Purpose:** Bridge the gap between AI agents and the live web — AgentChrome exposes structured, machine-readable browser state so agents can navigate pages, extract content, fill forms, and capture screenshots without fragile HTML scraping.

**Key characteristics:**
- Single Rust binary — no Node.js, no Python, no runtime dependencies
- Communicates with Chrome via the Chrome DevTools Protocol (CDP)
- Outputs structured JSON responses suitable for programmatic parsing
- Sub-50 ms startup time; binary under 10 MB
- Dual-licensed MIT / Apache 2.0

**Repository:** [https://github.com/Nunley-Media-Group/AgentChrome](https://github.com/Nunley-Media-Group/AgentChrome)

## Why AgentChrome

The research agent and other web-fetching workflows in Parsidion CC use `agentchrome dom get-html` as their primary page-retrieval method, piped through `html-to-md.py` to produce clean, noise-free markdown for LLM consumption.

```mermaid
graph LR
    Agent[Research Agent]
    AC[agentchrome dom get-html]
    H2M[html-to-md.py]
    Vault[Vault Note]

    Agent --> AC
    AC --> H2M
    H2M --> Vault

    style Agent fill:#4a148c,stroke:#9c27b0,stroke-width:2px,color:#ffffff
    style AC fill:#e65100,stroke:#ff9800,stroke-width:3px,color:#ffffff
    style H2M fill:#0d47a1,stroke:#2196f3,stroke-width:2px,color:#ffffff
    style Vault fill:#1b5e20,stroke:#4caf50,stroke-width:2px,color:#ffffff
```

Without AgentChrome, the research agent falls back to `curl` — which works but returns raw HTML that is noisier and harder for an LLM to parse. AgentChrome returns the fully rendered DOM after JavaScript execution, which is essential for single-page applications and documentation sites that rely on client-side rendering.

## Installation

### Via Cargo (build from source)

Requires Rust:

```bash
cargo install agentchrome
```

### Pre-built Binaries

Download the latest release binary for your platform from the [GitHub Releases page](https://github.com/Nunley-Media-Group/AgentChrome/releases):

| Platform | Architecture |
|----------|-------------|
| macOS | Apple Silicon (ARM64) |
| macOS | Intel (x86_64) |
| Linux | x86_64 |
| Linux | ARM64 |
| Windows | x86_64 |

Place the binary somewhere on your `PATH` (e.g., `/usr/local/bin/agentchrome` on macOS/Linux).

### Runtime Requirement

AgentChrome requires **Chrome or Chromium** to be installed and accessible. On macOS, a standard Google Chrome installation is sufficient.

### Verify Installation

```bash
agentchrome --version
agentchrome --help
```

## Key Capabilities

| Feature | Description |
|---------|-------------|
| **Page text extraction** | Extracts visible text from the rendered page |
| **DOM HTML extraction** | Retrieves outer HTML of any element after JavaScript execution |
| **Accessibility tree snapshots** | Returns stable UIDs for reliable element targeting (`page snapshot`) |
| **Screenshot capture** | Full-page or viewport PNG screenshots |
| **Form automation** | Fill form fields by UID or CSS selector (`form fill`, `form fill-many`) |
| **JavaScript execution** | Run arbitrary JS in the page context (`js exec`) |
| **Network monitoring** | Inspect requests and responses |
| **Cookie management** | List, set, delete, and clear browser cookies (`cookie`) |
| **Device emulation** | Simulate mobile viewports |
| **Performance tracing** | Capture Core Web Vitals |
| **Dialog handling** | Auto-accept or dismiss alerts and confirmations |

## Usage in Parsidion CC

### Research Agent Page Fetching

The primary use case is fetching pages for the research agent. Connect once per session, then navigate and extract HTML:

```bash
# Connect once per research session (launch headless Chrome)
agentchrome connect --launch --headless

# Navigate to a URL
agentchrome navigate "https://example.com/docs" --wait-until networkidle

# Fetch raw HTML and convert to clean markdown
agentchrome dom get-html "css:html" | uv run --script ~/.claude/skills/parsidion-cc/scripts/html-to-md.py - --url "https://example.com/docs" > /tmp/page-content.md
```

Then read `/tmp/page-content.md` for the cleaned content.

The research agent (`~/.claude/agents/research-agent.md`) uses this pipeline automatically when agentchrome is available, falling back to `curl` otherwise.

### Manual Page Inspection

```bash
# Get visible text of the current active tab
agentchrome page text

# Get raw HTML of the entire page
agentchrome dom get-html "css:html"

# Save a screenshot (full page)
agentchrome page screenshot --full-page --file screenshot.png

# Get accessibility tree (structured element list with UIDs)
agentchrome page snapshot
```

### Integration with html-to-md.py

`html-to-md.py` is designed to work with agentchrome output:

```bash
# Navigate then fetch and convert to markdown
agentchrome navigate https://docs.example.com/api
agentchrome dom get-html "css:html" | uv run --script ~/.claude/skills/parsidion-cc/scripts/html-to-md.py - --url https://docs.example.com/api
```

The `--url` flag is optional but improves link resolution in the markdown output.

## Common Commands

```bash
# Connect to (or launch) Chrome
agentchrome connect
agentchrome connect --launch --headless

# Navigate to a URL
agentchrome navigate https://example.com

# Extract visible text from the page
agentchrome page text

# Extract raw HTML of the full page
agentchrome dom get-html "css:html"

# Take a full-page screenshot
agentchrome page screenshot --full-page --file screenshot.png

# Get accessibility tree (assigns UIDs to elements)
agentchrome page snapshot

# Interact with an element (requires UID from page snapshot)
agentchrome interact click <element-uid>
agentchrome interact type "hello world"

# Execute JavaScript
agentchrome js exec "document.title"

# List available command examples
agentchrome examples
```

## Troubleshooting

### `agentchrome: command not found`

The binary is not on your `PATH`. Either install via `cargo install agentchrome` or download a pre-built binary and move it to a directory on your `PATH`.

### Chrome not found

AgentChrome looks for Chrome or Chromium in standard installation paths. If you use a non-standard location, check the agentchrome `--help` output for a `--browser-path` flag or equivalent configuration.

### Falls back to curl in the research agent

If you see curl being used instead of agentchrome, it means `agentchrome` is not found on the `PATH` that Claude Code uses. Verify:

```bash
# Check if agentchrome is accessible
which agentchrome

# Test directly
agentchrome --version
```

If `which agentchrome` returns a path but Claude Code still falls back to curl, your shell `PATH` may differ from Claude Code's environment. Add the binary's parent directory to the `PATH` in your shell profile (`.zshrc`, `.bashrc`, etc.).

### CDP connection errors

If agentchrome cannot connect to Chrome, ensure:
- Chrome or Chromium is installed
- No firewall rule blocks localhost CDP connections
- You are not running in a sandboxed environment that restricts browser access
- Use `agentchrome connect --status` to check the current connection state

## Related Documentation

- [docs/ARCHITECTURE.md](ARCHITECTURE.md) — System architecture, including the research agent that uses agentchrome
- [docs/MCPL.md](MCPL.md) — MCP Launchpad CLI: alternative search tools used alongside agentchrome
- [README.md](../README.md) — Project overview and prerequisites
