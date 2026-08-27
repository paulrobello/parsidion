# Contributing to Parsidion

Thank you for your interest in contributing to Parsidion. This guide covers the development setup, coding constraints, testing workflow, and PR expectations.

## Table of Contents
- [Prerequisites](#prerequisites)
- [Development Setup](#development-setup)
- [Coding Constraints](#coding-constraints)
- [Making Changes](#making-changes)
- [Testing Hooks Manually](#testing-hooks-manually)
- [Benchmarking Hooks (on-demand)](#benchmarking-hooks-on-demand)
- [Commit Conventions](#commit-conventions)
- [Pull Request Process](#pull-request-process)

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) for script execution and package management
- [bun](https://bun.sh/) for the Vault Visualizer — `make checkall` runs `make visualizer-check`, which runs `bunx tsc --noEmit`, `bun run lint`, and `bun test`. Without bun the gate cannot pass locally.
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

Any script under `skills/parsidion/scripts/` **must use Python stdlib exclusively**, except the **seven** PEP 723 scripts listed below, which declare their own inline dependencies via a `# /// script` block. `install.py` at the repo root follows the same stdlib-only constraint. No `pip install`, no `uv add`. The `pyproject.toml` intentionally has no runtime dependencies.

**Why:** Hook scripts run inside Claude Code's lifecycle events. Adding third-party dependencies would break the zero-dependency guarantee and complicate installation.

**PEP 723 exception list** (verified by `grep -lE "^# /// script" skills/parsidion/scripts/*.py tools/eval/*.py`):

| Script | Inline dep |
|---|---|
| `summarize_sessions.py` | `anyio` |
| `build_embeddings.py` | `fastembed`, `sqlite-vec`, `pillow` |
| `vault_embed_serve.py` | `fastembed` |
| `vault_search.py` | `fastembed`, `sqlite-vec` |
| `vault_stats.py` | (PEP 723 metadata-only; no third-party runtime imports) |
| `build_graph.py` | `numpy` |
| `html-to-md.py` | `beautifulsoup4`, `html2text` |

The **eval harness** under `tools/eval/` is also PEP 723 (developer-only, outside the stdlib gate's scope):

| Script | Inline dep |
|---|---|
| `tools/eval/embed_eval.py` | `fastembed`, `sqlite-vec`, `rich` |
| `tools/eval/embed_eval_common.py` | (shared inline metadata) |
| `tools/eval/embed_eval_generate.py` | (shared inline metadata) |
| `tools/eval/embed_eval_report.py` | (shared inline metadata) |
| `tools/eval/embed_eval_run.py` | `fastembed`, `sqlite-vec` |
| `tools/eval/prompt_eval_run.py` | `rich`, `pyyaml` |

`vault_new.py` is **not** on this list — it is stdlib-only and has no `# /// script` block. If you add a new PEP 723 script, append it here and update `CLAUDE.md`'s "Exceptions" bullet to match.

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

   On **macOS/Linux** the installer symlinks `~/.claude/skills/parsidion` back to this
   checkout, so edits under `skills/` are already live — no sync needed. The copy form
   applies to **Windows only** (where the installer falls back to `shutil.copytree`):
   ```bash
   cp skills/parsidion/scripts/vault_common.py ~/.claude/skills/parsidion/scripts/vault_common.py
   ```

3. **Run quality checks before committing:**
   ```bash
   make checkall
   uv run pre-commit run --all-files
   ```

### Editing a vault-note prompt (ENH-008)

The six AI prompts live as versioned template files under
`skills/parsidion/templates/prompts/`, rendered through the strict-variable
loader in `skills/parsidion/scripts/prompt_templates.py`. See
[docs/PROMPTS.md](docs/PROMPTS.md) for the template format, the strict variable
contract, version bump rules, and how to run the opt-in eval harness
(`tools/eval/prompt_eval_run.py`) against the golden transcript set before
landing a prompt change. The byte-identical rendering gate
(`tests/test_prompt_templates.py -k identical`) must stay green — separate the
mechanical move of text from any wording change into distinct commits.

### Cross-language parity fixtures (ENH-005)

Two contracts are shared between Python and TypeScript and must stay in sync:

- **Vault resolution** — resolution is single-sourced in Python: `core/vault_path.py:resolve_vault_server()` (the narrower server contract: named vaults + default + `VAULT_ROOT`) is the canonical resolver, and `visualizer/lib/vaultResolver.ts:resolveVault()` **delegates** to it via the stdlib `vault_resolve.py` CLI rather than reimplementing the rules (ENH-009 — there is no second implementation to drift). The shared observable behaviour is pinned by a single vector set at `tests/fixtures/parity/vault-resolution.json`, consumed by both `tests/test_vault_resolver_parity.py` and `visualizer/lib/vaultResolver.parity.test.ts`. **Changing the resolver requires updating the fixture** — add or edit a vector, then run both test files. Every vector runs on both sides unless it carries an explicit `applies_to` (and each suite asserts no vector is silently skipped).
- **`graph.json` schema** — `tests/fixtures/graph.schema.json` is *generated* from `GRAPH_JSON_SCHEMA` in `skills/parsidion/scripts/build_graph.py`. Do not hand-edit the fixture; run `make parity-fixtures` after changing `GRAPH_JSON_SCHEMA`. CI runs `make parity-fixtures-check` (regenerate-to-temp + diff) and fails on drift.

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

## Benchmarking Hooks (on-demand)

`make bench-hooks` (ENH-023) measures SessionStart hook latency against generated synthetic vaults (500 and 5000 notes) and fails when a size's median wall time exceeds its budget. It prints the per-stage breakdown the hook now logs (`stages_ms`: seed / semantic / graph / delta / ai / assemble) and appends each run to the gitignored `tools/bench/results.jsonl` trend file.

**When to run it:** before releases, and after any change to `session_start_hook.py`, the `session_start/` package, or the `vault_index.py` retrieval helpers — the paths that decide what every session start pays. It is deliberately **not** part of `make checkall` or CI: absolute budgets are machine-dependent, so the gate only means something on the machine you run it on.

```bash
make bench-hooks                                     # both sizes, 5 reps each
make bench-hooks BENCH_ARGS="--sizes 500 --reps 3"   # override via BENCH_ARGS
uv run --no-project tools/bench/bench_session_start.py --slow-ms 1500   # gate self-test
```

The budgets live in `tools/bench/bench_session_start.py` (`_BUDGET_MS`), calibrated on first run (see the comment there for date/machine). Recalibrate deliberately when the hook's workload changes — never to make a regression pass.

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
