# Contributing to Parsidion

Thank you for your interest in contributing to Parsidion. This guide covers the development setup, coding constraints, testing workflow, and PR expectations.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Development Setup](#development-setup)
- [Coding Constraints](#coding-constraints)
- [Making Changes](#making-changes)
- [Testing Hooks Manually](#testing-hooks-manually)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for script execution and package management
- [Obsidian](https://obsidian.md/) (optional, for vault browsing and graph view)

## Development Setup

1. **Fork and clone the repository:**
   ```bash
   git clone https://github.com/<your-username>/parsidion.git
   cd parsidion
   ```

2. **Install dev dependencies:**
   ```bash
   uv sync --group dev
   ```

3. **Install the local git hooks:**
   ```bash
   uv run pre-commit install
   ```

4. **Install the skill to your local Claude config (optional, for live testing):**
   ```bash
   uv run install.py --force --yes
   ```

5. **Run the quality checks:**
   ```bash
   make checkall
   uv run pre-commit run --all-files
   ```

## Coding Constraints

### stdlib-only rule

Any script under `skills/parsidion/scripts/` **must use Python stdlib exclusively**, except the four PEP 723 scripts (`summarize_sessions.py`, `build_embeddings.py`, `vault_search.py`, `vault_new.py`) which declare their own inline dependencies. `install.py` at the repo root follows the same stdlib-only constraint. No `pip install`, no `uv add`. The `pyproject.toml` intentionally has no runtime dependencies.

**Why:** Hook scripts run inside Claude Code's lifecycle events. Adding third-party dependencies would break the zero-dependency guarantee and complicate installation.

**Exception:** The four PEP 723 scripts listed above have inline dependency declarations (e.g. `claude-agent-sdk`, `anyio`, `fastembed`). Their dependencies are installed automatically by `uv run` into an isolated environment.

### Type annotations

Use modern Python type annotations throughout:
- Built-in generics: `list`, `dict`, `tuple`, `set` (not `List`, `Dict`, etc.)
- Union operator: `str | None` (not `Optional[str]`)
- Google-style docstrings on all public functions

### File I/O

- Always specify `encoding='utf-8'` when opening files
- Use `pathlib.Path` for all path operations

## Making Changes

1. **Edit source files** under `skills/`, `agents/`, or the project root.

2. **Sync to the installed location** after editing:
   ```bash
   uv run install.py --force --yes
   ```

   For a single-file quick sync:
   ```bash
   cp skills/parsidion/scripts/vault_common.py ~/.claude/skills/parsidion/scripts/vault_common.py
   ```

3. **Run quality checks before committing:**
   ```bash
   make checkall
   uv run pre-commit run --all-files
   ```

## Testing Hooks Manually

Hooks communicate via JSON on stdin/stdout. Use heredocs to avoid shell quoting issues:

```bash
# Test session_start_hook
python skills/parsidion/scripts/session_start_hook.py <<'EOF'
{"cwd": "/Users/yourname/Repos/myproject"}
EOF

# Test session_stop_hook (requires a real transcript path)
python skills/parsidion/scripts/session_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test session_stop_hook with a pi transcript path
python skills/parsidion/scripts/session_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/Users/you/.pi/agent/sessions/--path--/session.jsonl"}
EOF

# Test pre_compact_hook
python skills/parsidion/scripts/pre_compact_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test session_stop_wrapper (outputs {} immediately, spawns Python hook detached)
bash skills/parsidion/scripts/session_stop_wrapper.sh <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test post_compact_hook (reads last Pre-Compact Snapshot from today's daily note)
python skills/parsidion/scripts/post_compact_hook.py <<'EOF'
{"cwd": "/path/to/project", "transcript_path": "/path/to/transcript.jsonl"}
EOF

# Test subagent_stop_hook (requires a real agent_transcript_path)
python skills/parsidion/scripts/subagent_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "agent_transcript_path": "/path/to/agent.jsonl", "agent_id": "abc-123", "agent_type": "Explore"}
EOF

# Test subagent_stop_hook with a pi subagent transcript
python skills/parsidion/scripts/subagent_stop_hook.py <<'EOF'
{"cwd": "/path/to/project", "agent_transcript_path": "/Users/you/.pi/agent/sessions/--path--/subagent-xyz.jsonl", "agent_id": "xyz", "agent_type": "Explore"}
EOF
```

## Commit Conventions

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <subject>
```

### Types

| Type | Description |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation changes only |
| `style` | Formatting, whitespace (no logic change) |
| `refactor` | Code restructuring (no behavior change) |
| `test` | Adding or updating tests |
| `chore` | Maintenance, tooling, config changes |
| `perf` | Performance improvement |

### Rules

- Subject line: max 50 characters, imperative mood, no trailing period
- Body (optional): wrap at 72 characters, explain *what* and *why*
- Footer (optional): reference issues (`Closes #123`)
- Keep commits atomic -- one logical change per commit

### Examples

```
feat(hooks): add AI-powered note selection to session start hook
fix(vault_common): handle UnicodeDecodeError in read_last_n_lines
docs(readme): add troubleshooting section
chore: add Makefile with standard quality targets
```

## Pull Request Process

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make your changes** and ensure all checks pass:
   ```bash
   make checkall
   ```

3. **Push and open a PR** against `main`.

4. **PR expectations:**
   - Clear title following conventional commit format
   - Description explaining what changed and why
   - All CI checks passing
   - Maintain the stdlib-only constraint for hook scripts

5. **Merge strategy:** PRs are squash-merged to keep the main branch history clean. The squash commit message should summarize all changes in the PR.

## Code of Conduct

Be respectful, constructive, and collaborative. We are all here to build something useful.
