# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-03-15

### Added
- `uv tool install` support — `pyproject.toml` now declares a `vault-search` `[project.scripts]` entry point and a `[tools]` optional-dependency group (`fastembed`, `sqlite-vec`); running `uv tool install --editable ".[tools]"` from the repo (or `uv run install.py --install-tools`) makes `vault-search` globally available on all platforms without PATH manipulation
- `install.py --install-tools` flag — calls `uv tool install --editable ".[tools]"` automatically as step 11; without the flag, the next-steps summary prints the manual command
- `note_index` table in `embeddings.db` — populated by `update_index.py` on every index rebuild; stores per-note metadata (stem, path, folder, title, summary, tags, type, project, confidence, mtime, related, is_stale, incoming_links) with 5 secondary indexes for sub-millisecond queries without O(n) file walks
- `vault_search.py` merged with former `vault_query.py` — single unified CLI with two modes: semantic (positional `QUERY` string, uses fastembed + sqlite-vec) and metadata (filter flags `--tag`/`--folder`/`--type`/`--project`/`--recent-days`, queries `note_index` table); both modes share identical JSON output with `score` field (`null` for metadata results); mutually exclusive — error if both a query and filter flags are provided
- `ensure_note_index_schema(conn)` in `vault_common.py` — creates `note_index` table and all 5 indexes; called by `build_embeddings.py` `open_db()` so the schema is guaranteed from first DB creation
- `query_note_index(*, tag, folder, note_type, project, recent_days, limit)` in `vault_common.py` — DB-first query; opens DB read-only; returns `None` (not `[]`) when absent or table missing to signal fallback to file walk; uses 4-pattern LIKE for exact tag-token matching (avoids `python` matching `python-decorator`)
- DB-first pattern for `find_notes_by_tag()`, `find_notes_by_project()`, `find_notes_by_type()`, and `find_recent_notes()` in `vault_common.py` — try `query_note_index()` first; fall back to O(n) file walk transparently when DB absent
- `_write_note_index_to_db()` in `update_index.py` — upserts all note rows into `note_index`, prunes rows for deleted notes, swallows all exceptions (never crashes the indexer)
- `subagent_stop_hook.py` — new `SubagentStop` hook (registered with `async: true`) that captures subagent transcripts and queues them for AI summarization via the same `pending_summaries.jsonl` pipeline as the SessionEnd hook
- `excluded_agents` config key under `subagent_stop_hook` (default: `"vault-explorer,research-documentation-agent"`) to prevent recursive capture of vault system agents
- `TRANSCRIPT_CATEGORIES`, `TRANSCRIPT_CATEGORY_LABELS`, `parse_transcript_lines()`, `detect_categories()`, and `append_to_pending()` moved to `vault_common.py` (shared between `session_stop_hook.py` and `subagent_stop_hook.py`)
- `source` and `agent_type` fields in `pending_summaries.jsonl` entries for traceability (`source: "session"` or `"subagent"`)
- `subagent_stop_hook` config section in `config.yaml` template with `enabled`, `min_messages`, and `excluded_agents` keys
- `_HOOK_OPTIONS` dict in `install.py` for per-event hook handler options (enables `async: true` on SubagentStop)
- `[tool.ty.environment]` `extra-paths` in `pyproject.toml` so `ty` resolves `vault_common` from source
- PID singleton guard in `update_index.py` to prevent concurrent runs (mirrors `vault_doctor.py` pattern)
- CONTRIBUTING.md with development setup and PR guidelines
- CHANGELOG.md following Keep a Changelog format
- Makefile with standard quality targets (`lint`, `fmt`, `typecheck`, `test`, `checkall`)
- Unit test suite with 61 tests covering core vault_common functions
- Troubleshooting section in README
- Quick Start and Prerequisites sections in README
- Table of Contents in README
- `__all__` declaration in vault_common.py
- `env_without_claudecode()` helper in vault_common.py
- `--help` flag for `scripts/show-context`
- `related` field guidance in daily note template
- pyright extraPaths config for test module resolution

### Changed
- `vault-explorer.md` agent now has a 7-step workflow with a new Tier 2 metadata search step (step 2) using `vault-search` filter flags between semantic search and the CLAUDE.md+grep fallback; existing steps 2–6 renumbered to 3–7
- Consolidated `extract_text_from_content()` and `read_last_n_lines()` into vault_common.py (was duplicated across hooks)
- Consolidated file locking functions (`flock_exclusive`, `flock_shared`, `funlock`) into vault_common.py
- Replaced `asyncio.gather()` with `anyio.create_task_group()` in summarize_sessions.py
- Replaced `f.readlines()[-n:]` with `collections.deque(f, maxlen=n)` in `read_last_n_lines`
- Extracted `_find_notes_by_field()` generic helper from near-duplicate find_notes functions
- Enhanced `parse_frontmatter()` with multi-line scalar block support; `run_trigger_eval.py` now reuses it
- Reconciled VAULT_DIRS lists between install.py and vault_common.py
- Renamed `_c` to `_colorize` in install.py
- Filter subprocess environment to safe vars only (PATH, HOME, etc.) instead of full passthrough
- Expanded AGENTS.md and GEMINI.md stubs with redirect pattern explanation
- Added upper bounds on PEP 723 dependencies (`claude-agent-sdk<1.0`, `anyio<5.0`)
- `migrate_research.py` accepts research path as CLI argument instead of hardcoding
- `check_graph_coverage.py` imports VAULT_ROOT from vault_common instead of hardcoding
- `migrate_memory.py` computes TODAY at point of use instead of module level

### Fixed
- Added `session_stop_wrapper.sh` shell wrapper for SessionEnd hook; outputs `{}` immediately then runs `session_stop_hook.py` detached via `nohup`, preventing "Hook cancelled" errors when Claude Code exits before `uv run` starts up
- `install.py` now registers `session_stop_wrapper.sh` for SessionEnd (not the Python script directly) and makes `.sh` files executable during install
- Changed `permission_mode` from `bypassPermissions` to `default` in summarize_sessions.py
- Replaced MD5 with SHA-256 for content hashing in migrate_research.py
- Set 0o600 permissions on debug log file in session_start_hook.py
- Added `traceback.print_exc()` to all hook exception handlers (errors no longer silently swallowed)
- Added stderr warnings for unparsable YAML config lines
- Fixed pyproject.toml project name typo (`parsidian-cc` → `parsidion-cc`)
- Fixed type narrowing for `dict[str, object].get()` calls in session_stop_hook.py and summarize_sessions.py
- CLAUDE.md architecture section correctly describes four layers instead of three
- Documentation style guide references Parsidion CC instead of wrong project name
- Line number references removed from CLAUDE.md Key File Paths section
- History folder added to SKILL.md vault structure and update_index.py FOLDER_ORDER
- Added missing .gitignore entries (`*-mcp.json`, `.gemini-clipboard`, `claude_scratch/`, etc.)
- Documented show-context script in ARCHITECTURE.md
- Moved `html-to-md` from `scripts/` to `skills/claude-vault/scripts/html-to-md.py`; added `.py` extension (it is a PEP 723 Python script); updated research agent, ARCHITECTURE.md, and slideshows to reference the new path (`~/.claude/skills/claude-vault/scripts/html-to-md.py`)

## [0.1.0] - 2026-03-10

### Added
- Claude Vault skill (`skills/claude-vault/`) with Obsidian-backed knowledge management
- Session lifecycle hooks: SessionStart, SessionEnd, PreCompact
- AI-powered note selection via `--ai [MODEL]` flag on session start hook
- AI-powered session classification via `--ai [MODEL]` flag on session stop hook
- Session summarizer (`summarize_sessions.py`) using Claude Agent SDK for structured note generation
- Shared library (`vault_common.py`) with frontmatter parsing, vault traversal, config loader, and git integration
- Vault index generator (`update_index.py`) with tag cloud, recent activity, and per-folder listings
- Graph coverage checker (`check_graph_coverage.py`) for auditing Obsidian color groups
- Trigger evaluation harness (`run_trigger_eval.py`) for measuring skill invocation accuracy
- Research documentation agent (`agents/research-documentation-agent.md`)
- Installer (`install.py`) with vault path validation, dry-run mode, and uninstall support
- Centralized configuration via `~/ClaudeVault/config.yaml` with three-tier precedence
- Optional vault git integration with auto-commit support
- 8 note templates (daily, project, language, framework, pattern, debugging, tool, research)
- Architecture documentation with Mermaid diagrams (`docs/ARCHITECTURE.md`)
