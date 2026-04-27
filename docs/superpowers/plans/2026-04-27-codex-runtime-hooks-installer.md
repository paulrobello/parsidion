# Codex Runtime Hooks Installer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Codex runtime hooks and installer runtime selection while preserving existing Claude behavior.

**Architecture:** Keep Claude hook registration unchanged and add a separate Codex adapter path. Codex support consists of focused hook wrappers, Codex transcript parsing/path validation helpers, and installer functions that surgically merge `~/.codex/hooks.json` and enable `codex_hooks` in `~/.codex/config.toml`.

**Tech Stack:** Python 3.13, stdlib installer, pytest, ruff, pyright, existing Parsidion hook utilities.

---

## File Structure

- Modify `install.py` — runtime selection CLI/prompt, Codex hook/config merge/uninstall, install/uninstall flow branching.
- Modify `skills/parsidion/scripts/vault_hooks.py` — Codex transcript root helper and Codex rollout JSONL parser.
- Modify `skills/parsidion/scripts/vault_common.py` — re-export new Codex helpers.
- Create `skills/parsidion/scripts/codex_session_start_hook.py` — Codex `SessionStart` wrapper.
- Create `skills/parsidion/scripts/codex_stop_hook.py` — Codex `Stop` wrapper.
- Modify `tests/test_install.py` — installer runtime/Codex hook/config tests.
- Modify `tests/test_vault_common.py` — transcript root/parser tests.
- Modify `tests/test_hook_integration.py` — Codex hook subprocess integration tests.
- Modify `README.md`, `SECURITY.md`, `skills/parsidion/SKILL.md`, `CHANGELOG.md` — document runtime selection and Codex behavior.

---

### Task 1: Runtime Selection CLI and Defaults

**Files:**
- Modify: `install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for parse args and runtime default resolution**

Add tests near `TestParseArgs`:

```python
def test_parse_args_supports_runtime_and_codex_home(self, monkeypatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["install.py", "--runtime", "both", "--codex-home", "~/CustomCodex"],
    )

    args = install.parse_args()

    assert args.runtime == "both"
    assert args.codex_home == "~/CustomCodex"


def test_resolve_runtime_defaults_to_claude_for_yes() -> None:
    assert install.resolve_runtime_choice(runtime=None, yes=True, interactive=False) == "claude"


def test_resolve_runtime_defaults_to_both_for_interactive(monkeypatch) -> None:
    monkeypatch.setattr(install, "_ask", lambda prompt, default="": "")

    assert install.resolve_runtime_choice(runtime=None, yes=False, interactive=True) == "both"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_install.py::TestParseArgs -q
```

Expected: fails because `--runtime`, `--codex-home`, and `resolve_runtime_choice()` do not exist.

- [ ] **Step 3: Implement minimal runtime option support**

In `install.py` add constants:

```python
_RUNTIME_CHOICES = ("claude", "codex", "both", "none")
```

Add function:

```python
def resolve_runtime_choice(
    runtime: str | None,
    *,
    yes: bool,
    interactive: bool,
) -> str:
    """Resolve runtime selection for install/uninstall flows."""
    if runtime:
        return runtime
    if yes or not interactive:
        return "claude"

    print()
    print(bold("Runtime Integrations"))
    print(
        dim(
            "  1. Claude only — ~/.claude settings, skills, agents, and hooks.\n"
            "  2. Codex only — ~/.codex hooks for SessionStart and Stop.\n"
            "  3. Both Claude + Codex.\n"
            "  4. Shared tooling only — no runtime hooks."
        )
    )
    answer = _ask("Install runtime integrations", default="both").strip().lower()
    if answer in ("", "3", "both", "claude+codex", "claude + codex"):
        return "both"
    if answer in ("1", "claude", "claude only"):
        return "claude"
    if answer in ("2", "codex", "codex only"):
        return "codex"
    if answer in ("4", "none", "shared", "shared tooling only"):
        return "none"
    _warn(f"Unknown runtime selection {answer!r}; defaulting to both")
    return "both"
```

Add argparse flags:

```python
parser.add_argument(
    "--runtime",
    choices=_RUNTIME_CHOICES,
    default=None,
    help=(
        "Runtime integration target: claude, codex, both, or none. "
        "Interactive default is both; --yes default is claude for backwards compatibility."
    ),
)
parser.add_argument(
    "--codex-home",
    metavar="PATH",
    default=os.environ.get("CODEX_HOME", "~/.codex"),
    help="Codex home directory for hooks/config (default: $CODEX_HOME or ~/.codex)",
)
```

Import `os` at top-level if needed, or use existing local import only after adding top-level import.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_install.py::TestParseArgs -q
```

Expected: parse/runtime tests pass.

- [ ] **Step 5: Commit**

```bash
git add install.py tests/test_install.py
git commit -m "feat: add runtime selection options"
```

---

### Task 2: Codex Hook Config Merge and Uninstall

**Files:**
- Modify: `install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing tests for Codex hooks/config**

Add `TestCodexHooks` to `tests/test_install.py`:

```python
class TestCodexHooks:
    def test_merge_codex_hooks_creates_hooks_json(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)

        hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
        assert "SessionStart" in hooks["hooks"]
        assert "Stop" in hooks["hooks"]
        commands = [
            hook["command"]
            for group in hooks["hooks"].values()
            for entry in group
            for hook in entry["hooks"]
        ]
        assert any("codex_session_start_hook.py" in command for command in commands)
        assert any("codex_stop_hook.py" in command for command in commands)

    def test_merge_codex_hooks_preserves_existing_hooks_and_is_idempotent(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        hooks_file = codex_home / "hooks.json"
        hooks_file.parent.mkdir(parents=True)
        hooks_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "",
                                "hooks": [{"type": "command", "command": "echo existing"}],
                            }
                        ]
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )

        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)

        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        handlers = hooks["hooks"]["SessionStart"]
        commands = [hook["command"] for entry in handlers for hook in entry["hooks"]]
        assert commands.count("echo existing") == 1
        assert sum("codex_session_start_hook.py" in command for command in commands) == 1

    def test_remove_codex_hooks_only_removes_managed_commands(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        claude_dir = tmp_path / ".claude"
        install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
        hooks_file = codex_home / "hooks.json"
        hooks = json.loads(hooks_file.read_text(encoding="utf-8"))
        hooks["hooks"].setdefault("Stop", []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}
        )
        hooks_file.write_text(json.dumps(hooks, indent=2) + "\n", encoding="utf-8")

        changed = install.remove_codex_hooks(codex_home, claude_dir, dry_run=False)

        updated = json.loads(hooks_file.read_text(encoding="utf-8"))
        assert changed is True
        assert updated["hooks"]["Stop"] == [
            {"matcher": "", "hooks": [{"type": "command", "command": "echo user"}]}
        ]

    def test_enable_codex_hooks_config_creates_features_section(self, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"

        install.enable_codex_hooks_config(codex_home, dry_run=False, yes=True)

        assert (codex_home / "config.toml").read_text(encoding="utf-8") == (
            "[features]\n"
            "codex_hooks = true\n"
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_install.py::TestCodexHooks -q
```

Expected: fails because Codex functions do not exist.

- [ ] **Step 3: Implement Codex hook/config functions**

Add constants/functions to `install.py` near Claude hook registration helpers:

```python
_CODEX_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "codex_session_start_hook.py",
    "Stop": "codex_stop_hook.py",
}


def _managed_codex_hook_command(claude_dir: Path, event: str) -> str:
    script = _CODEX_HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        script_display = f"~/{rel.as_posix()}"
    except ValueError:
        script_display = script_path.as_posix()
    return f"uv run --no-project {script_display}"
```

Implement `merge_codex_hooks()`, `remove_codex_hooks()`, and `enable_codex_hooks_config()` using the same `_filter_hook_entries()` helper style as Claude. Hook JSON shape:

```python
{
    "hooks": {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {"type": "command", "command": command, "timeout": 10000}
                ],
            }
        ]
    }
}
```

`enable_codex_hooks_config()` should handle missing file, existing `[features]`, absent `codex_hooks`, and `codex_hooks = false` replacement. If the file contains no `[features]`, append:

```toml

[features]
codex_hooks = true
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_install.py::TestCodexHooks -q
```

Expected: all Codex hook/config tests pass.

- [ ] **Step 5: Commit**

```bash
git add install.py tests/test_install.py
git commit -m "feat: manage codex hook registration"
```

---

### Task 3: Codex Transcript Roots and Parser

**Files:**
- Modify: `skills/parsidion/scripts/vault_hooks.py`
- Modify: `skills/parsidion/scripts/vault_common.py`
- Test: `tests/test_vault_common.py`

- [ ] **Step 1: Write failing tests**

Add tests to `tests/test_vault_common.py` near transcript helper tests:

```python
class TestCodexTranscriptHelpers:
    def test_allowed_transcript_roots_includes_codex_sessions(self, monkeypatch, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        roots = vault_common.allowed_transcript_roots(cwd=str(tmp_path))

        assert codex_home.resolve() / "sessions" in roots

    def test_is_codex_transcript_path(self, monkeypatch, tmp_path: Path) -> None:
        codex_home = tmp_path / ".codex"
        transcript = codex_home / "sessions" / "2026" / "04" / "27" / "rollout-test.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("", encoding="utf-8")
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        assert vault_common.is_codex_transcript_path(transcript)
        assert vault_common.is_allowed_transcript_path(transcript, cwd=str(tmp_path))

    def test_parse_codex_transcript_lines_extracts_assistant_text(self) -> None:
        lines = [
            '{"type":"response_item","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Fixed the parser bug"}]}}',
            '{"type":"response_item","item":{"type":"message","role":"user","content":[{"type":"input_text","text":"hello"}]}}',
            '{"type":"unknown","value":1}',
            'not json',
        ]

        assert vault_common.parse_codex_transcript_lines(lines) == ["Fixed the parser bug"]
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_vault_common.py::TestCodexTranscriptHelpers -q
```

Expected: fails because Codex helpers/parser do not exist or roots are missing.

- [ ] **Step 3: Implement helpers and parser**

In `vault_hooks.py`, add:

```python
def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def is_codex_transcript_path(path: Path) -> bool:
    try:
        resolved = path.expanduser().resolve()
        root = (codex_home() / "sessions").resolve()
        return resolved == root or resolved.is_relative_to(root)
    except OSError:
        return False
```

Update `allowed_transcript_roots()` to include `codex_home() / "sessions"`.

Add parser:

```python
def parse_codex_transcript_lines(lines: list[str]) -> list[str]:
    texts: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        item = record.get("item") if isinstance(record, dict) else None
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message" or item.get("role") != "assistant":
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            if content.strip():
                texts.append(content.strip())
            continue
        if not isinstance(content, list):
            continue
        chunks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"output_text", "text"}:
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
        if chunks:
            texts.append("\n".join(chunks))
    return texts
```

Export `is_codex_transcript_path` and `parse_codex_transcript_lines` from `vault_common.py` and `__all__`.

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_vault_common.py::TestCodexTranscriptHelpers -q
```

Expected: tests pass.

- [ ] **Step 5: Commit**

```bash
git add skills/parsidion/scripts/vault_hooks.py skills/parsidion/scripts/vault_common.py tests/test_vault_common.py
git commit -m "feat: parse codex transcripts"
```

---

### Task 4: Codex Hook Wrapper Scripts

**Files:**
- Create: `skills/parsidion/scripts/codex_session_start_hook.py`
- Create: `skills/parsidion/scripts/codex_stop_hook.py`
- Test: `tests/test_hook_integration.py`

- [ ] **Step 1: Write failing integration tests**

Add tests to `tests/test_hook_integration.py`:

```python
@pytest.mark.timeout(15)
class TestCodexHookIntegration:
    def test_codex_session_start_stdout_is_valid_json(self, tmp_path: Path) -> None:
        result = _run_hook(
            "codex_session_start_hook.py",
            {"cwd": str(tmp_path), "hook_event_name": "SessionStart", "transcript_path": None},
            tmp_path,
        )

        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert isinstance(parsed, dict)

    def test_codex_stop_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "codex_stop_hook.py",
            {"cwd": str(tmp_path), "hook_event_name": "Stop", "transcript_path": None},
            tmp_path,
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_codex_stop_with_real_transcript_queues_pending(self, tmp_path: Path, monkeypatch) -> None:
        codex_home = tmp_path / ".codex"
        transcript = codex_home / "sessions" / "2026" / "04" / "27" / "rollout-test.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            '{"type":"response_item","item":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Fixed a pytest failure by updating the parser test."}]}}\n',
            encoding="utf-8",
        )

        result = _run_hook(
            "codex_stop_hook.py",
            {"cwd": str(tmp_path), "hook_event_name": "Stop", "transcript_path": str(transcript)},
            tmp_path,
            extra_env={"CODEX_HOME": str(codex_home)},
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        assert "rollout-test" in pending.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_hook_integration.py::TestCodexHookIntegration -q
```

Expected: fails because scripts do not exist.

- [ ] **Step 3: Implement `codex_session_start_hook.py`**

Create a script that:

- imports existing `session_start_hook.build_session_context`
- reads stdin JSON
- resolves `cwd` with fallback to `Path.cwd()`
- calls `build_session_context(cwd, ai_model=None, max_chars=config value or default, verbose_mode=False)`
- writes valid JSON. Use the same Claude-compatible shape initially because Codex accepts hook JSON and this keeps context isolated:

```python
{"additionalContext": context}
```

On exceptions, print traceback to stderr and write `{}`.

- [ ] **Step 4: Implement `codex_stop_hook.py`**

Create a script modeled on `session_stop_hook.py` but smaller:

- read stdin JSON
- skip if `PARSIDION_INTERNAL` is set
- require `transcript_path`
- validate with `vault_common.is_allowed_transcript_path()` and `vault_common.is_codex_transcript_path()`
- resolve vault via `vault_common.resolve_vault(cwd=cwd)`
- read tail lines with `vault_common.read_last_n_lines()`
- parse with `vault_common.parse_codex_transcript_lines()`
- detect categories with `vault_common.detect_categories()`
- update daily note with `vault_common.append_session_to_daily()`
- queue with `vault_common.append_to_pending(transcript_path, project, categories, force=True, vault=vault_path)` when categories exist
- write `{}` for all success/skip cases

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_hook_integration.py::TestCodexHookIntegration -q
```

Expected: Codex hook tests pass.

- [ ] **Step 6: Commit**

```bash
git add skills/parsidion/scripts/codex_session_start_hook.py skills/parsidion/scripts/codex_stop_hook.py tests/test_hook_integration.py
git commit -m "feat: add codex runtime hooks"
```

---

### Task 5: Wire Runtime Selection Into Install/Uninstall Flow

**Files:**
- Modify: `install.py`
- Test: `tests/test_install.py`

- [ ] **Step 1: Write failing flow tests**

Add tests to `tests/test_install.py`:

```python
def test_runtime_none_skips_hook_registration(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "ClaudeVault"
    monkeypatch.setattr(sys, "argv", ["install.py", "--yes", "--runtime", "none", "--vault", str(vault), "--claude-dir", str(tmp_path / ".claude")])
    args = install.parse_args()

    assert install.resolve_runtime_choice(args.runtime, yes=args.yes, interactive=False) == "none"


def test_runtime_both_installs_codex_hooks_in_dry_run(tmp_path: Path) -> None:
    codex_home = tmp_path / ".codex"
    claude_dir = tmp_path / ".claude"

    install.merge_codex_hooks(codex_home, claude_dir, dry_run=True, verbose=False)

    assert not (codex_home / "hooks.json").exists()
```

If direct `install.install(args)` integration tests are practical, add one dry-run test that sets `--runtime both --dry-run --vault ... --claude-dir ... --codex-home ...` and asserts exit code `0`.

- [ ] **Step 2: Run targeted tests to verify RED or existing gap**

Run:

```bash
uv run pytest tests/test_install.py -q
```

Expected: new flow tests expose missing runtime flow integration where applicable.

- [ ] **Step 3: Update install flow**

In `install(args)`:

- resolve `runtime = resolve_runtime_choice(args.runtime, yes=args.yes, interactive=not args.yes)` after vault path resolution
- resolve `codex_home = Path(args.codex_home).expanduser().resolve()`
- derive booleans:

```python
install_claude_runtime = runtime in ("claude", "both")
install_codex_runtime = runtime in ("codex", "both")
install_runtime_hooks = runtime != "none" and not args.skip_hooks
```

- print selected runtime(s) in installation plan
- run Claude-specific install steps only when `install_claude_runtime`:
  - agents
  - Claude hooks
  - `CLAUDE-VAULT.md`
- keep shared skill/scripts/vault setup because current scripts live under Claude skill path
- run Codex hook/config steps when `install_codex_runtime and not args.skip_hooks`:

```python
enable_codex_hooks_config(codex_home, dry_run=dry_run, yes=args.yes)
merge_codex_hooks(codex_home, claude_dir, dry_run=dry_run, verbose=verbose)
```

In `main()` uninstall path:

- resolve runtime same as install, with interactive default `both` when not `--yes`
- pass runtime and codex_home into `uninstall()`
- update `uninstall()` signature and remove Claude/Codex assets according to runtime selection

- [ ] **Step 4: Run targeted tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_install.py -q
```

Expected: install tests pass.

- [ ] **Step 5: Commit**

```bash
git add install.py tests/test_install.py
git commit -m "feat: wire installer runtime selection"
```

---

### Task 6: Documentation and Changelog

**Files:**
- Modify: `README.md`
- Modify: `SECURITY.md`
- Modify: `skills/parsidion/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update docs**

Add concise documentation:

- README install section:

```markdown
### Runtime integrations

Interactive installs ask which runtime integrations to configure:

- `claude` — Claude Code skill, agents, and hooks under `~/.claude`
- `codex` — Codex CLI hooks under `~/.codex`
- `both` — both integrations
- `none` — shared vault tooling only

Non-interactive installs keep the historical default:

```bash
uv run install.py --yes --runtime claude
uv run install.py --yes --runtime both
uv run install.py --yes --runtime codex
```

Codex integration uses native Codex hooks and requires `codex_hooks = true` in `~/.codex/config.toml`. Parsidion can enable this during install. Parsidion does not manage Codex auth or copy `~/.codex/auth.json`.
```

- SECURITY mention `~/.codex/hooks.json` and `~/.codex/config.toml` as modified surfaces.
- SKILL mention Codex runtime hooks are session lifecycle only.
- CHANGELOG add unreleased entry for Codex runtime hooks and runtime selector.

- [ ] **Step 2: Run docs grep checks**

Run:

```bash
rg -n "--runtime|codex_hooks|hooks.json|~/.codex" README.md SECURITY.md skills/parsidion/SKILL.md CHANGELOG.md
```

Expected: all four docs have relevant references.

- [ ] **Step 3: Commit**

```bash
git add README.md SECURITY.md skills/parsidion/SKILL.md CHANGELOG.md
git commit -m "docs: document codex runtime integration"
```

---

### Task 7: Final Verification and Integration

**Files:**
- All changed files

- [ ] **Step 1: Run targeted verification**

```bash
uv run pytest tests/test_install.py tests/test_hook_integration.py tests/test_vault_common.py
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run full verification**

```bash
make checkall
```

Expected: ruff format/check pass, pyright reports 0 errors, pytest passes.

- [ ] **Step 3: Inspect diff**

```bash
git diff --stat main...HEAD
git diff --check main...HEAD
```

Expected: no whitespace errors; diff limited to spec, plan, installer, hook scripts/helpers/tests/docs.

- [ ] **Step 4: Final commit if needed**

If verification caused formatting changes:

```bash
git add -A
git commit -m "chore: finalize codex runtime integration"
```

- [ ] **Step 5: Report branch status**

```bash
git status --short --branch
git log --oneline --decorate -8
```

Expected: clean worktree on `feature/codex-runtime-hooks`.
