# Parsidion Rename and Legacy Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the current `parsidion-cc` codebase to `parsidion` and make the installer automatically clean legacy managed `parsidion-cc` assets.

**Architecture:** This plan implements Phases 1 and 2 from `docs/superpowers/specs/2026-04-26-parsidion-rebrand-design.md`. It keeps current Claude Code behavior intact, changes install/package/source paths to `parsidion`, and confines old-name references to explicit legacy cleanup code/tests/history. Provider abstraction, OpenAI provider support, and Codex runtime support are intentionally separate follow-up plans.

**Tech Stack:** Python 3.13, stdlib installer, pytest, ruff, pyright, uv, Claude Code hook JSON config.

---

## Scope Boundaries

This plan covers:

- hard rename from `parsidion-cc` to `parsidion`
- source directory move from `skills/parsidion-cc/` to `skills/parsidion/`
- package/test/import path updates
- installer legacy cleanup for managed `parsidion-cc` hooks/assets
- docs/runtime path updates needed for a coherent rename

This plan does not cover:

- new `llm/` provider abstraction
- OpenAI provider implementation
- Codex hooks/transcript parsing
- GitHub repository rename operations outside the working tree

## File Structure Map

### Primary files to modify

- `install.py` — installer constants, hook command construction, scheduler paths, uninstall, legacy cleanup helpers, CLI text.
- `pyproject.toml` — package name, setuptools script path, test/coverage/typecheck paths.
- `tests/test_install.py` — installer behavior tests for new paths and legacy cleanup.
- `tests/test_session_start_hook.py`, `tests/test_pre_compact_hook.py`, `tests/test_hook_integration.py` — script import path updates.
- `CLAUDE.md`, `AGENTS.md`, `README.md`, `SECURITY.md`, `CONTRIBUTING.md`, `CLAUDE-VAULT.md`, `docs/**/*.md`, `skills/parsidion/SKILL.md`, `skills/parsidion/templates/config.yaml`, `extensions/pi/parsidion/*.md`, `extensions/pi/parsidion/*.ts`, `extensions/pi/parsidion/*.test.ts` — rename docs and runtime references where not intentionally legacy.
- `CHANGELOG.md` — add a migration warning and allow historical `parsidion-cc` references to remain.

### Paths to rename

- Rename directory: `skills/parsidion-cc/` → `skills/parsidion/`.
- Do not rename binary/static image assets in this implementation pass. Update markdown references if those assets still use old URLs or alt text.

### References allowed to keep `parsidion-cc`

Only these locations may contain `parsidion-cc` after implementation:

- legacy cleanup constants/functions/tests in `install.py` and `tests/test_install.py`
- the design/plan docs for this migration
- `CHANGELOG.md` historical entries
- git internals ignored by normal search
- external URLs that still intentionally point to the old repository until the repository is renamed

---

## Task 1: Add failing installer rename and legacy cleanup tests

**Files:**
- Modify: `tests/test_install.py`

- [ ] **Step 1: Add tests for new install path, legacy hook cleanup, and legacy asset cleanup**

Append these tests to `tests/test_install.py`:

```python

class TestParsidionRenamePaths:
    """Tests for the hard rename from parsidion-cc to parsidion."""

    def test_hook_command_uses_parsidion_skill_path(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"

        command = install._hook_command(claude_dir, "SessionStart")

        assert "skills/parsidion/scripts/session_start_hook.py" in command
        assert "parsidion-cc" not in command

    def test_install_skill_uses_parsidion_destination(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        vault_root = tmp_path / "ClaudeVault"

        dest = install.install_skill(
            claude_dir,
            vault_root,
            dry_run=True,
            force=True,
            verbose=False,
        )

        assert dest == claude_dir / "skills" / "parsidion"


class TestLegacyCleanup:
    """Tests for automatic cleanup of managed parsidion-cc assets."""

    def test_cleanup_legacy_hooks_removes_old_commands_only(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_command = (
            "uv run --no-project "
            "~/.claude/skills/parsidion-cc/scripts/session_start_hook.py"
        )
        new_command = install._hook_command(claude_dir, "SessionStart")
        settings = {
            "theme": "dark",
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": legacy_command,
                                "timeout": 10000,
                            }
                        ],
                    },
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "echo keep-me",
                                "timeout": 1000,
                            }
                        ],
                    },
                ],
                "SessionEnd": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": new_command,
                                "timeout": 10000,
                            }
                        ],
                    }
                ],
            },
        }
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
        )

        assert changed is True
        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert updated["theme"] == "dark"
        session_start = updated["hooks"]["SessionStart"]
        assert session_start == [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo keep-me",
                        "timeout": 1000,
                    }
                ],
            }
        ]
        assert updated["hooks"]["SessionEnd"] == settings["hooks"]["SessionEnd"]

    def test_cleanup_legacy_assets_removes_old_skill_dir(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_skill = claude_dir / "skills" / "parsidion-cc"
        legacy_skill.mkdir(parents=True)
        (legacy_skill / "SENTINEL.txt").write_text("legacy\n", encoding="utf-8")
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text('{"hooks": {}}\n', encoding="utf-8")

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=False,
            verbose=False,
        )

        assert changed is True
        assert not legacy_skill.exists()

    def test_cleanup_legacy_assets_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        claude_dir = tmp_path / ".claude"
        settings_file = claude_dir / "settings.json"
        legacy_skill = claude_dir / "skills" / "parsidion-cc"
        legacy_skill.mkdir(parents=True)
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "uv run --no-project ~/.claude/skills/parsidion-cc/scripts/session_start_hook.py",
                                        "timeout": 10000,
                                    }
                                ],
                            }
                        ]
                    }
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        changed = install.cleanup_legacy_assets(
            claude_dir,
            settings_file,
            dry_run=True,
            verbose=False,
        )

        assert changed is True
        assert legacy_skill.exists()
        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert "parsidion-cc" in updated["hooks"]["SessionStart"][0]["hooks"][0]["command"]
```

- [ ] **Step 2: Run the new tests and verify they fail**

Run:

```bash
uv run pytest tests/test_install.py -v
```

Expected now:

- `test_hook_command_uses_parsidion_skill_path` fails because `_hook_command()` still points to `skills/parsidion-cc`.
- `test_install_skill_uses_parsidion_destination` fails because `install_skill()` still returns `skills/parsidion-cc`.
- cleanup tests fail because `cleanup_legacy_assets()` does not exist yet.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_install.py
git commit -m "test: cover parsidion rename installer cleanup"
```

---

## Task 2: Rename the skill source directory and package metadata

**Files:**
- Rename: `skills/parsidion-cc/` → `skills/parsidion/`
- Modify: `pyproject.toml`
- Modify: path imports in tests that import scripts directly

- [ ] **Step 1: Move the skill directory**

Run:

```bash
git mv skills/parsidion-cc skills/parsidion
```

Expected: `git status --short` shows a rename from `skills/parsidion-cc/...` to `skills/parsidion/...` for every tracked skill file.

- [ ] **Step 2: Update `pyproject.toml` paths and package name**

Edit `pyproject.toml` so these sections have exactly these values:

```toml
[project]
name = "parsidion"
version = "0.5.6"
requires-python = ">=3.13"
dependencies = []
```

```toml
[project.optional-dependencies]
search = [
    "fastembed>=0.6.0,<1.0",
    "sqlite-vec>=0.1.6,<1.0",
    "pillow>=12.2.0",
]
tools = [
    "parsidion[search]",
    "rich>=13.0",
]
```

```toml
[tool.setuptools.package-dir]
"" = "skills/parsidion/scripts"
```

```toml
[tool.pytest.ini_options]
testpaths = [
    "tests",
]
addopts = "-v --cov=skills/parsidion/scripts --cov-report=term-missing"
timeout = 10
```

```toml
[tool.pyright]
extraPaths = [
    "skills/parsidion/scripts",
]
exclude = [
    "parsidion-mcp",
    ".worktrees",
    ".venv",
    "skills/parsidion/scripts/build_graph.py",
]
```

```toml
[tool.ty.environment]
extra-paths = [
    "skills/parsidion/scripts",
]
```

- [ ] **Step 3: Update tests that add script directories to `sys.path`**

Replace direct script path construction in these files:

- `tests/test_session_start_hook.py`
- `tests/test_pre_compact_hook.py`
- `tests/test_hook_integration.py`

The path expression should use `skills/parsidion/scripts`:

```python
_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
```

If a file uses `sys.path.insert(0, str(... / "skills" / "parsidion-cc" / "scripts"))`, replace only the path component with `"parsidion"`.

- [ ] **Step 4: Run import-focused tests**

Run:

```bash
uv run pytest tests/test_session_start_hook.py tests/test_pre_compact_hook.py tests/test_hook_integration.py -v
```

Expected: tests import hook scripts from `skills/parsidion/scripts`. Some failures are acceptable only if they are caused by installer constants still pointing to the legacy path; import errors are not acceptable.

- [ ] **Step 5: Commit the directory/package rename**

```bash
git add pyproject.toml tests/test_session_start_hook.py tests/test_pre_compact_hook.py tests/test_hook_integration.py skills/parsidion
git add -u skills/parsidion-cc
git commit -m "chore: rename skill package to parsidion"
```

---

## Task 3: Update installer constants and new-path behavior

**Files:**
- Modify: `install.py`

- [ ] **Step 1: Add explicit current and legacy name constants**

Near the source layout constants in `install.py`, replace the hard-coded skill source with constants:

```python
PROJECT_NAME = "parsidion"
LEGACY_PROJECT_NAME = "parsidion-cc"
SKILL_NAME = PROJECT_NAME
LEGACY_SKILL_NAME = LEGACY_PROJECT_NAME

REPO_ROOT: Path = Path(__file__).parent.resolve()
SKILL_SRC: Path = REPO_ROOT / "skills" / SKILL_NAME
LEGACY_SKILL_SRC: Path = REPO_ROOT / "skills" / LEGACY_SKILL_NAME
```

Keep `AGENT_SRCS`, `SCRIPTS_SRC`, and `CLAUDE_VAULT_MD_SRC` immediately after these constants.

- [ ] **Step 2: Update installer destination paths**

Change `install_skill()` so the destination is:

```python
dest = claude_dir / "skills" / SKILL_NAME
```

Change its docstring first sentence to:

```python
"""Install skill to ~/.claude/skills/parsidion/."""
```

- [ ] **Step 3: Update hook command path construction**

Change `_hook_command()` so it builds paths with `SKILL_NAME`:

```python
script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
```

- [ ] **Step 4: Update installer-owned script/template paths**

Update all installer paths that currently build `claude_dir / "skills" / "parsidion-cc"` to use `SKILL_NAME`, including:

```python
scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
script = claude_dir / "skills" / SKILL_NAME / "scripts" / "update_index.py"
templates_src = claude_dir / "skills" / SKILL_NAME / "templates"
scripts_dir = claude_dir / "skills" / SKILL_NAME / "scripts"
```

These correspond to scheduler generation, index rebuild, template symlink creation, and post-merge hook installation.

- [ ] **Step 5: Update config directory paths from `parsidion-cc` to `parsidion`**

In `uninstall()` and `create_vaults_config()`, update user config paths to:

```python
vaults_config = Path.home() / ".config" / PROJECT_NAME / "vaults.yaml"
config_dir = Path.home() / ".config" / PROJECT_NAME
```

Update the generated vaults config header to:

```python
content = """# Named vaults for parsidion
# Use with: vault-search --vault NAME or CLAUDE_VAULT=NAME
```

- [ ] **Step 6: Update scheduler markers/log names to `parsidion`**

Change these constants/strings:

```python
_CRON_MARKER = "# parsidion: nightly summarizer"
```

Launchd stdout/stderr paths should use:

```python
Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"
```

Cron log path should use:

```python
_cron_log = Path.home() / ".claude" / "logs" / "parsidion-summarizer.log"
```

Post-merge marker/template should use:

```python
_POST_MERGE_MARKER = "# parsidion post-merge hook"
```

Template output lines should say `[parsidion]`, for example:

```bash
echo "[parsidion] Rebuilding vault index..."
echo "[parsidion] Updating embeddings (incremental)..."
echo "[parsidion] Post-merge sync complete."
```

- [ ] **Step 7: Update installer user-facing text**

Replace current product text in `install.py` with Parsidion wording:

```python
print(dim("Skills, hooks, and knowledge vault for coding agents"))
```

```python
parser = argparse.ArgumentParser(
    prog="install.py",
    description="Install Parsidion skills, hooks, and vault tooling.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
    add_help=False,
)
```

Where the install summary prints the skill path, use:

```python
print(f"  {dim('Install skill:')} {claude_dir / 'skills' / SKILL_NAME}")
```

Next-step commands should use:

```python
uv run ~/.claude/skills/parsidion/scripts/update_index.py
uv run ~/.claude/skills/parsidion/scripts/build_embeddings.py
```

- [ ] **Step 8: Run installer tests and verify new-path tests pass except legacy cleanup**

Run:

```bash
uv run pytest tests/test_install.py -v
```

Expected:

- `TestParsidionRenamePaths` passes.
- `TestLegacyCleanup` still fails until Task 4 implements `cleanup_legacy_assets()`.
- Existing uninstall tests may fail if they still create `skills/parsidion-cc`; update them in the next step.

- [ ] **Step 9: Update existing uninstall tests for the new current skill path**

In `tests/test_install.py`, update existing current-skill setup from:

```python
skill_dir = claude_dir / "skills" / "parsidion-cc"
```

to:

```python
skill_dir = claude_dir / "skills" / "parsidion"
```

Do not change the new legacy cleanup tests; they intentionally create `parsidion-cc` paths.

- [ ] **Step 10: Commit installer new-path changes**

```bash
git add install.py tests/test_install.py
git commit -m "chore: update installer paths for parsidion"
```

---

## Task 4: Implement automatic legacy cleanup

**Files:**
- Modify: `install.py`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Add legacy hook command detection helpers**

Add these helpers near `_hook_command()` in `install.py`:

```python
def _legacy_hook_command_fragment(event: str) -> str:
    """Return the legacy managed command path fragment for a hook event."""
    script = _HOOK_SCRIPTS[event]
    return f"skills/{LEGACY_SKILL_NAME}/scripts/{script}"


def _is_legacy_managed_hook_command(command: str, event: str) -> bool:
    """Return True when *command* is a managed parsidion-cc legacy hook."""
    return _legacy_hook_command_fragment(event) in command.replace("\\", "/")
```

- [ ] **Step 2: Add a hook-list filtering helper shared by uninstall and cleanup**

Add below `_hook_already_registered()`:

```python
def _filter_hook_entries(
    event_hooks: list[dict],
    predicate,
) -> tuple[list[dict], bool]:
    """Remove hook handlers matching *predicate* while preserving unrelated hooks.

    Empty hook entries are removed. Returns the filtered entries and whether
    anything changed.
    """
    filtered_entries: list[dict] = []
    changed = False

    for entry in event_hooks:
        hooks = entry.get("hooks", [])
        if not isinstance(hooks, list):
            filtered_entries.append(entry)
            continue

        kept_hooks = []
        for hook in hooks:
            if isinstance(hook, dict) and predicate(hook):
                changed = True
                continue
            kept_hooks.append(hook)

        if kept_hooks:
            new_entry = dict(entry)
            new_entry["hooks"] = kept_hooks
            filtered_entries.append(new_entry)
        else:
            changed = True

    return filtered_entries, changed
```

- [ ] **Step 3: Refactor `remove_installed_hooks()` to use `_filter_hook_entries()`**

Inside `remove_installed_hooks()`, replace the existing list-comprehension filtering block with:

```python
for event, _script_name in _HOOK_SCRIPTS.items():
    command = _hook_command(claude_dir, event)
    event_hooks: list[dict] = hooks_section.get(event, [])
    filtered, event_changed = _filter_hook_entries(
        event_hooks,
        lambda hook, command=command: hook.get("command", "") == command,
    )
    if event_changed:
        _step(f"Remove hook {bold(event)}", dry_run=dry_run)
        changed = True
        if filtered:
            hooks_section[event] = filtered
        elif event in hooks_section:
            del hooks_section[event]
```

Run `uv run pytest tests/test_install.py::TestUninstallHooksOnly -v` after this refactor. Expected: existing uninstall-hooks behavior still passes.

- [ ] **Step 4: Implement legacy hook cleanup**

Add this function below `remove_installed_hooks()`:

```python
def remove_legacy_hooks(
    settings_file: Path,
    dry_run: bool = False,
) -> bool:
    """Remove managed legacy parsidion-cc hook registrations from settings.json."""
    if not settings_file.exists():
        return False

    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read settings.json for legacy cleanup: {exc}")
        return False

    hooks_section: dict = settings.get("hooks", {})
    changed = False

    for event, _script_name in _HOOK_SCRIPTS.items():
        event_hooks: list[dict] = hooks_section.get(event, [])
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, event=event: _is_legacy_managed_hook_command(
                str(hook.get("command", "")), event
            ),
        )
        if event_changed:
            _step(f"Remove legacy hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            settings_file.write_text(
                json.dumps(settings, indent=2) + "\n", encoding="utf-8"
            )
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")

    return changed
```

- [ ] **Step 5: Implement legacy asset cleanup**

Add this function below `remove_legacy_hooks()`:

```python
def cleanup_legacy_assets(
    claude_dir: Path,
    settings_file: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """Remove managed legacy parsidion-cc hooks and installed skill assets.

    This preserves user vault contents and unrelated Claude settings.
    """
    changed = False

    if remove_legacy_hooks(settings_file, dry_run=dry_run):
        changed = True

    legacy_skill_dir = claude_dir / "skills" / LEGACY_SKILL_NAME
    if legacy_skill_dir.exists() or legacy_skill_dir.is_symlink():
        _step(f"Remove legacy skill {legacy_skill_dir}", dry_run=dry_run)
        changed = True
        if not dry_run:
            try:
                if legacy_skill_dir.is_symlink() or legacy_skill_dir.is_file():
                    legacy_skill_dir.unlink()
                else:
                    shutil.rmtree(legacy_skill_dir)
            except OSError as exc:
                _warn(f"Could not remove legacy skill {legacy_skill_dir}: {exc}")
    else:
        _print(
            dim(f"  No legacy skill found at {legacy_skill_dir}"),
            verbose_only=True,
            verbose=verbose,
        )

    return changed
```

- [ ] **Step 6: Call cleanup before new hook registration during install**

In `run_install()`, after templates are linked and before `merge_hooks(...)`, call cleanup when hooks are not skipped:

```python
# 7. Clean up legacy managed parsidion-cc hooks/assets, then register hooks
if not args.skip_hooks:
    cleanup_legacy_assets(
        claude_dir,
        settings_file,
        dry_run=dry_run,
        verbose=verbose,
    )
    merge_hooks(claude_dir, settings_file, dry_run=dry_run, verbose=verbose)
```

Do not run legacy cleanup when `--skip-hooks` is set because the user explicitly requested no settings mutation. Full uninstall still handles legacy cleanup in the next step.

- [ ] **Step 7: Make uninstall clean both current and legacy assets**

In `uninstall()`, after current skill removal and before removing agents, add:

```python
legacy_skill_dir = claude_dir / "skills" / LEGACY_SKILL_NAME
if legacy_skill_dir.exists() or legacy_skill_dir.is_symlink():
    _step(f"Remove legacy skill {legacy_skill_dir}", dry_run=dry_run)
    if not dry_run:
        try:
            if legacy_skill_dir.is_symlink() or legacy_skill_dir.is_file():
                legacy_skill_dir.unlink()
            else:
                shutil.rmtree(legacy_skill_dir)
        except OSError as exc:
            _warn(f"Could not remove legacy skill {legacy_skill_dir}: {exc}")
```

After `remove_installed_hooks(...)`, call:

```python
remove_legacy_hooks(settings_file, dry_run=dry_run)
```

For `hooks_only=True`, call both current and legacy hook removal:

```python
remove_installed_hooks(claude_dir, settings_file, dry_run=dry_run)
remove_legacy_hooks(settings_file, dry_run=dry_run)
```

- [ ] **Step 8: Run installer tests**

Run:

```bash
uv run pytest tests/test_install.py -v
```

Expected: all tests in `tests/test_install.py` pass.

- [ ] **Step 9: Commit legacy cleanup implementation**

```bash
git add install.py tests/test_install.py
git commit -m "feat: clean legacy parsidion-cc install assets"
```

---

## Task 5: Update runtime script references and generated guidance paths

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`
- Modify: `skills/parsidion/scripts/*.py`
- Modify: `skills/parsidion/SKILL.md`
- Modify: `CLAUDE-VAULT.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: other docs found by grep

- [ ] **Step 1: Inventory remaining old-name references outside intentional history**

Run:

```bash
rg "parsidion-cc|Parsidion CC|Claude Vault|Claude Code-specific|skills/parsidion-cc" \
  --glob '!CHANGELOG.md' \
  --glob '!docs/superpowers/specs/2026-04-26-parsidion-rebrand-design.md' \
  --glob '!docs/superpowers/plans/2026-04-26-parsidion-rename-legacy-cleanup.md'
```

Expected: many matches before this task.

- [ ] **Step 2: Update summarizer progress filename**

In `skills/parsidion/scripts/summarize_sessions.py`, change:

```python
_PROGRESS_FILE = vault_common.secure_log_dir() / "parsidion-cc-summarizer-progress.json"
```

to:

```python
_PROGRESS_FILE = vault_common.secure_log_dir() / "parsidion-summarizer-progress.json"
```

- [ ] **Step 3: Update installed path examples in guidance docs**

Replace executable path examples in these files from `~/.claude/skills/parsidion-cc/...` to `~/.claude/skills/parsidion/...`:

- `CLAUDE-VAULT.md`
- `CLAUDE.md`
- `README.md`
- `skills/parsidion/SKILL.md`
- `docs/ARCHITECTURE.md`
- `docs/README.md`
- `docs/EMBEDDINGS.md`
- `docs/EMBEDDINGS_EVAL.md`
- `docs/VISUALIZER.md`
- `docs/VAULT_SYNC.md`
- `extensions/pi/parsidion/parsidion.md`
- `visualizer/CLAUDE.md`

Use this mechanical replacement for path examples:

```bash
python - <<'PY'
from pathlib import Path
files = [
    Path('CLAUDE-VAULT.md'),
    Path('CLAUDE.md'),
    Path('README.md'),
    Path('skills/parsidion/SKILL.md'),
    *Path('docs').glob('*.md'),
    Path('extensions/pi/parsidion/parsidion.md'),
    Path('visualizer/CLAUDE.md'),
]
for path in files:
    if not path.exists():
        continue
    text = path.read_text(encoding='utf-8')
    new = text.replace('~/.claude/skills/parsidion-cc/', '~/.claude/skills/parsidion/')
    new = new.replace('skills/parsidion-cc/', 'skills/parsidion/')
    if new != text:
        path.write_text(new, encoding='utf-8')
PY
```

- [ ] **Step 4: Update product positioning in README intro**

In `README.md`, replace the title and first positioning paragraphs with:

```markdown
# Parsidion

[![CI](https://github.com/paulrobello/parsidion/actions/workflows/ci.yml/badge.svg)](https://github.com/paulrobello/parsidion/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/Python-3.13%2B-blue.svg)

A second brain for coding agents -- a markdown knowledge vault that gives AI coding assistants persistent memory, cross-session context, and a searchable store of everything they learn. [Obsidian](https://obsidian.md/) is **not required** -- it is an optional viewer for graph visualization and browsing.

Parsidion replaces fragile, tool-specific memory with a richly organized markdown vault. Runtime adapters load relevant context at startup, capture durable learnings from sessions, and snapshot working state before compaction where supported. A research agent saves structured findings, and an AI-powered summarizer generates vault notes from session transcripts.
```

Keep the rest of README content, but update local references from `Parsidion CC` to `Parsidion` unless the sentence explicitly discusses legacy cleanup or history.

- [ ] **Step 5: Update project guidance title/summary**

In `CLAUDE.md`, update the opening project identity from Claude Code-specific wording to:

```markdown
# CLAUDE.md

This file provides guidance to AI coding assistants when working with code in this repository.

## Project Overview

Parsidion is the source repository for an agent-agnostic markdown knowledge vault: skills, agents, hook scripts, search/index tools, and visualizer/MCP integrations that give coding agents persistent memory. Claude Code remains the primary installed adapter today, but the core vault tooling is runtime-agnostic.
```

Do not delete detailed Claude Code hook documentation; update it to call that section the Claude Code adapter.

- [ ] **Step 6: Update AGENTS and security wording**

In `AGENTS.md`, replace the first sentence with:

```markdown
Parsidion ships subagents that coding-agent runtimes can dispatch during sessions.
```

In `SECURITY.md`, update the opening threat model to say Parsidion currently installs Claude Code hooks and will add additional runtime adapters. Keep explicit `~/.claude` details for the current adapter.

- [ ] **Step 7: Update TypeScript extension status labels that use the old name**

In `extensions/pi/parsidion/status.ts` and `status.test.ts`, keep `parsidion` extension naming. Replace comments/user-facing text that says `Parsidion CC` or references `skills/parsidion-cc`; leave type names such as `AnthropicStatusKey` unchanged because they describe provider config, not project branding.

Run:

```bash
rg "parsidion-cc|Parsidion CC|skills/parsidion-cc" extensions/pi/parsidion
```

Expected after edits: no matches unless a test explicitly validates legacy cleanup text.

- [ ] **Step 8: Run docs/reference grep**

Run:

```bash
rg "parsidion-cc|Parsidion CC|skills/parsidion-cc" \
  --glob '!CHANGELOG.md' \
  --glob '!docs/superpowers/specs/2026-04-26-parsidion-rebrand-design.md' \
  --glob '!docs/superpowers/plans/2026-04-26-parsidion-rename-legacy-cleanup.md' \
  --glob '!install.py' \
  --glob '!tests/test_install.py'
```

Expected: no matches, except external old GitHub URLs if they are deliberately retained until repository rename. If external URLs remain, add an inline comment or nearby sentence making clear they are legacy redirect URLs.

- [ ] **Step 9: Commit runtime/docs reference updates**

```bash
git add CLAUDE-VAULT.md CLAUDE.md AGENTS.md README.md SECURITY.md CONTRIBUTING.md docs extensions skills/parsidion visualizer
git commit -m "docs: rebrand project as parsidion"
```

---

## Task 6: Update remaining Python tests and source path references

**Files:**
- Modify: `tests/**/*.py`
- Modify: `skills/parsidion/scripts/**/*.py`
- Modify: `scripts/**/*` if referenced by grep

- [ ] **Step 1: Search code and tests for old source path references**

Run:

```bash
rg "parsidion-cc|skills/parsidion-cc|Parsidion CC" tests skills/parsidion scripts install.py pyproject.toml
```

Expected before this task: matches in intentional legacy cleanup plus some old path/test references.

- [ ] **Step 2: Update non-legacy test project-name fixtures**

In `tests/test_session_start_hook.py`, replace test-only project names from:

```python
project_name="parsidion-cc"
```

to:

```python
project_name="parsidion"
```

Do this only for tests that are not explicitly testing legacy cleanup.

- [ ] **Step 3: Update script path references in test setup**

Ensure all test script directories use:

```python
Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
```

Run:

```bash
rg "skills.*/.parsidion-cc|parsidion-cc.*/scripts" tests
```

Expected: no matches.

- [ ] **Step 4: Update migration script references that point at installed skill paths**

Search under `skills/parsidion/scripts`:

```bash
rg "parsidion-cc|skills/parsidion-cc|~/.claude/skills/parsidion-cc" skills/parsidion/scripts
```

For each non-legacy runtime path, update to `parsidion`. Keep strings in explicit historical migrations only if the code is migrating old vault notes or old research names; add a comment if retained:

```python
# Legacy source name retained to migrate pre-rename notes.
```

- [ ] **Step 5: Run Python tests likely affected by path changes**

Run:

```bash
uv run pytest \
  tests/test_install.py \
  tests/test_hook_integration.py \
  tests/test_session_start_hook.py \
  tests/test_pre_compact_hook.py \
  tests/test_vault_dirs_sync.py \
  -v
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit code/test reference cleanup**

```bash
git add tests skills/parsidion/scripts scripts install.py pyproject.toml
git commit -m "test: update parsidion source path references"
```

---

## Task 7: Add migration warning to changelog and verify install dry-run

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add changelog entry at the top**

Add this entry near the top of `CHANGELOG.md` under the current unreleased/newest section:

```markdown
## Unreleased

### Changed

- Rebranded the project from `parsidion-cc` to `parsidion`. New installs use `~/.claude/skills/parsidion/`, package metadata uses `parsidion`, and docs now describe Parsidion as an agent-agnostic memory/vault layer for coding assistants.

### Migration

- The installer now automatically removes managed legacy `parsidion-cc` hook registrations and the old `~/.claude/skills/parsidion-cc/` skill directory or symlink before registering new `parsidion` hooks. User vault contents under `~/ClaudeVault/` are preserved.
```

If `CHANGELOG.md` already has an `Unreleased` section, merge these bullets into it instead of creating a duplicate heading.

- [ ] **Step 2: Run install dry-run**

Run:

```bash
uv run install.py --dry-run --yes
```

Expected output includes:

- install skill path ending in `.claude/skills/parsidion`
- hook commands containing `skills/parsidion/scripts/`
- no new hook command containing `skills/parsidion-cc/scripts/`

- [ ] **Step 3: Run uninstall-hooks dry-run**

Run:

```bash
uv run install.py --uninstall-hooks --dry-run --yes
```

Expected output mentions removing current Parsidion hooks if present. It may also mention legacy cleanup if legacy hooks are present in the real user settings; absence is acceptable.

- [ ] **Step 4: Commit changelog/dry-run fixes**

If dry-run exposed any text/path issues, fix them. Then commit:

```bash
git add CHANGELOG.md install.py
git commit -m "docs: document parsidion rename migration"
```

---

## Task 8: Final verification and old-name audit

**Files:**
- No planned edits unless verification finds issues.

- [ ] **Step 1: Run formatting/lint/type/test verification**

Run the repository's top-level verification:

```bash
make checkall
```

Expected: all configured checks pass. If `make checkall` is not available or does not include all relevant checks, run:

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

- [ ] **Step 2: Run final old-name audit**

Run:

```bash
rg "parsidion-cc|Parsidion CC|skills/parsidion-cc|~/.claude/skills/parsidion-cc" \
  --glob '!CHANGELOG.md' \
  --glob '!docs/superpowers/specs/2026-04-26-parsidion-rebrand-design.md' \
  --glob '!docs/superpowers/plans/2026-04-26-parsidion-rename-legacy-cleanup.md' \
  --glob '!install.py' \
  --glob '!tests/test_install.py' \
  --glob '!.git/**'
```

Expected: no output. If output appears, either update it to `parsidion` or document why it is an intentional legacy/historical reference.

- [ ] **Step 3: Audit intentional legacy references**

Run:

```bash
rg "parsidion-cc|Parsidion CC|skills/parsidion-cc|~/.claude/skills/parsidion-cc" install.py tests/test_install.py CHANGELOG.md docs/superpowers/specs/2026-04-26-parsidion-rebrand-design.md docs/superpowers/plans/2026-04-26-parsidion-rename-legacy-cleanup.md
```

Expected: matches only describe legacy cleanup, migration history, or this implementation plan.

- [ ] **Step 4: Inspect git status and recent commits**

Run:

```bash
git status --short
git log --oneline -8
```

Expected:

- working tree clean
- commits from this plan are present in logical order

- [ ] **Step 5: Commit any final verification fixes**

If Step 1 or Step 2 required fixes, commit them:

```bash
git add <fixed-files>
git commit -m "fix: complete parsidion rename verification"
```

If no fixes were required, do not create an empty commit.
