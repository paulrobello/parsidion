# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
