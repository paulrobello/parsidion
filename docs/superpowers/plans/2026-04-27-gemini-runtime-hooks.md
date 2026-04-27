# Gemini Runtime Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Gemini CLI runtime hook and transcript support to Parsidion, with installer runtime selection for Claude, Codex, Gemini, all, or none.

**Architecture:** Mirror the existing Codex runtime integration: add small Gemini wrapper scripts for `SessionStart` and `SessionEnd`, add Gemini transcript helpers to `vault_hooks.py`/`vault_common.py`, and extend `install.py` with a separate Gemini settings merge/remove path. Keep Gemini runtime hooks separate from prompt AI backend selection.

**Tech Stack:** Python 3.13 stdlib hook scripts, JSON settings merge, pytest subprocess integration tests, ruff, pyright.

---

## File Structure

- Modify `install.py`
  - Add runtime choices `gemini` and `all`.
  - Add Gemini hook command construction and settings merge/remove helpers.
  - Wire Gemini into install/uninstall flows and dry-run plan output.
- Modify `skills/parsidion/scripts/vault_hooks.py`
  - Add `gemini_home()`, `is_gemini_transcript_path()`, and `parse_gemini_transcript_lines()`.
  - Include Gemini roots in `allowed_transcript_roots()`.
- Modify `skills/parsidion/scripts/vault_common.py`
  - Re-export Gemini transcript helpers.
- Create `skills/parsidion/scripts/gemini_session_start_hook.py`
  - Gemini `SessionStart` wrapper around existing Parsidion context builder.
- Create `skills/parsidion/scripts/gemini_session_end_hook.py`
  - Gemini `SessionEnd` wrapper for transcript parsing, category detection, daily note update, and pending queueing.
- Modify tests:
  - `tests/test_install.py`
  - `tests/test_vault_common.py`
  - `tests/test_hook_integration.py`
- Modify docs:
  - `README.md`
  - `skills/parsidion/SKILL.md`
  - `docs/ARCHITECTURE.md`
  - `CHANGELOG.md`

---

### Task 1: Runtime CLI choices and installer menu

**Files:**
- Modify: `install.py`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Write failing runtime-choice tests**

Append/modify tests in `tests/test_install.py` under `TestParseArgs`:

```python
def test_parse_args_supports_gemini_and_all_runtime(self, monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["install.py", "--runtime", "gemini"])
    args = install.parse_args()
    assert args.runtime == "gemini"

    monkeypatch.setattr(sys, "argv", ["install.py", "--runtime", "all"])
    args = install.parse_args()
    assert args.runtime == "all"


def test_resolve_runtime_interactive_accepts_gemini_and_all(self, monkeypatch) -> None:
    monkeypatch.setattr(install, "_ask", lambda prompt, default="": "3")
    assert install.resolve_runtime_choice(runtime=None, yes=False, interactive=True) == "gemini"

    monkeypatch.setattr(install, "_ask", lambda prompt, default="": "5")
    assert install.resolve_runtime_choice(runtime=None, yes=False, interactive=True) == "all"


def test_resolve_runtime_keeps_yes_default_claude(self) -> None:
    assert install.resolve_runtime_choice(runtime=None, yes=True, interactive=False) == "claude"
```

- [ ] **Step 2: Run RED test**

```bash
cd /Users/probello/Repos/parsidion/.worktrees/gemini-runtime-hooks
uv run pytest tests/test_install.py::TestParseArgs -q
```

Expected: `--runtime gemini/all` fails argument validation, or interactive mapping fails.

- [ ] **Step 3: Update runtime constants and parser**

In `install.py`, change:

```python
_RUNTIME_CHOICES = ("claude", "codex", "both", "none")
```

to:

```python
_RUNTIME_CHOICES = ("claude", "codex", "gemini", "both", "all", "none")
```

Update the interactive menu in `resolve_runtime_choice()` to show explicit choices:

```python
print(
    dim(
        "  1. Claude only — ~/.claude settings, skills, agents, and hooks.\n"
        "  2. Codex only — ~/.codex hooks for SessionStart and Stop.\n"
        "  3. Gemini only — ~/.gemini settings hooks for SessionStart and SessionEnd.\n"
        "  4. Claude + Codex.\n"
        "  5. All runtimes — Claude + Codex + Gemini.\n"
        "  6. Shared tooling only — no runtime hooks."
    )
)
answer = _ask("Install runtime integrations", default="both").strip().lower()
if answer in ("", "4", "both", "claude+codex", "claude + codex"):
    return "both"
if answer in ("1", "claude", "claude only"):
    return "claude"
if answer in ("2", "codex", "codex only"):
    return "codex"
if answer in ("3", "gemini", "gemini only"):
    return "gemini"
if answer in ("5", "all", "all runtimes", "claude+codex+gemini"):
    return "all"
if answer in ("6", "none", "shared", "shared tooling only"):
    return "none"
_warn(f"Unknown runtime selection {answer!r}; defaulting to both")
return "both"
```

Keep `yes`/non-interactive default as `claude`.

- [ ] **Step 4: Run GREEN test**

```bash
uv run pytest tests/test_install.py::TestParseArgs -q
```

Expected: all `TestParseArgs` tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
uv run ruff format install.py tests/test_install.py
uv run ruff check install.py tests/test_install.py
uv run pyright install.py tests/test_install.py
uv run pytest tests/test_install.py::TestParseArgs -q
git add install.py tests/test_install.py
git commit -m "feat: add gemini runtime choices"
```

---

### Task 2: Gemini settings merge/remove helpers

**Files:**
- Modify: `install.py`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Write failing Gemini hook merge tests**

Add a new test class in `tests/test_install.py` after `TestCodexHooks`:

```python
class TestGeminiHooks:
    def test_merge_gemini_hooks_creates_settings_json(self, tmp_path: Path) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"

        install.merge_gemini_hooks(gemini_home, claude_dir, dry_run=False, verbose=False)

        settings = json.loads((gemini_home / "settings.json").read_text(encoding="utf-8"))
        assert "SessionStart" in settings["hooks"]
        assert "SessionEnd" in settings["hooks"]
        commands = [
            hook["command"]
            for group in settings["hooks"].values()
            for entry in group
            for hook in entry["hooks"]
        ]
        assert any("gemini_session_start_hook.py" in command for command in commands)
        assert any("gemini_session_end_hook.py" in command for command in commands)

    def test_merge_gemini_hooks_preserves_existing_settings_and_is_idempotent(
        self, tmp_path: Path
    ) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"
        settings_file = gemini_home / "settings.json"
        settings_file.parent.mkdir(parents=True)
        settings_file.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [{"type": "command", "command": "echo existing"}],
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        install.merge_gemini_hooks(gemini_home, claude_dir, dry_run=False, verbose=False)
        install.merge_gemini_hooks(gemini_home, claude_dir, dry_run=False, verbose=False)

        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        assert settings["theme"] == "dark"
        commands = [
            hook["command"]
            for entry in settings["hooks"]["SessionStart"]
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        ]
        assert commands.count("echo existing") == 1
        assert sum("gemini_session_start_hook.py" in command for command in commands) == 1

    def test_remove_gemini_hooks_only_removes_managed_commands(self, tmp_path: Path) -> None:
        gemini_home = tmp_path / ".gemini"
        claude_dir = tmp_path / ".claude"
        install.merge_gemini_hooks(gemini_home, claude_dir, dry_run=False, verbose=False)
        settings_file = gemini_home / "settings.json"
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
        settings["hooks"].setdefault("SessionEnd", []).append(
            {"matcher": "*", "hooks": [{"type": "command", "command": "echo user"}]}
        )
        settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

        changed = install.remove_gemini_hooks(gemini_home, claude_dir, dry_run=False)

        updated = json.loads(settings_file.read_text(encoding="utf-8"))
        assert changed is True
        commands = [
            hook["command"]
            for entries in updated["hooks"].values()
            for entry in entries
            if isinstance(entry, dict)
            for hook in entry.get("hooks", [])
            if isinstance(hook, dict)
        ]
        assert "echo user" in commands
        assert not any("gemini_session_" in command for command in commands)
```

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest tests/test_install.py::TestGeminiHooks -q
```

Expected: fails because `merge_gemini_hooks` and helpers do not exist.

- [ ] **Step 3: Add Gemini constants and command helper**

In `install.py` near `_CODEX_HOOK_SCRIPTS`, add:

```python
_GEMINI_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "gemini_session_start_hook.py",
    "SessionEnd": "gemini_session_end_hook.py",
}
```

Add helper near `_managed_codex_hook_command()`:

```python
def _managed_gemini_hook_command(claude_dir: Path, event: str) -> str:
    """Return the managed Gemini hook command string for a Gemini event."""
    script = _GEMINI_HOOK_SCRIPTS[event]
    script_path = claude_dir / "skills" / SKILL_NAME / "scripts" / script
    try:
        rel = script_path.relative_to(Path.home())
        script_display = f"~/{rel}"
    except ValueError:
        script_display = str(script_path)
    return f"uv run --no-project {script_display}"
```

Add:

```python
def _gemini_settings_file(gemini_home: Path) -> Path:
    return gemini_home / "settings.json"
```

- [ ] **Step 4: Add shared JSON read helper or Gemini-specific reader**

Add Gemini-specific reader near Codex reader:

```python
def _read_gemini_settings(settings_file: Path) -> dict | None:
    """Read Gemini settings JSON, returning None when unsafe to edit."""
    if not settings_file.exists():
        return {"hooks": {}}
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        _warn(f"Could not read {settings_file}: {exc}; skipping Gemini hook update")
        return None
    if not isinstance(settings, dict):
        _warn(f"{settings_file} is not a JSON object; skipping Gemini hook update")
        return None
    hooks_section = settings.setdefault("hooks", {})
    if not isinstance(hooks_section, dict):
        _warn(f"{settings_file} has non-object hooks section; skipping Gemini hook update")
        return None
    return settings
```

- [ ] **Step 5: Implement merge/remove helpers**

Add:

```python
def merge_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Merge Parsidion-managed Gemini hooks into GEMINI_HOME/settings.json."""
    settings_file = _gemini_settings_file(gemini_home)
    settings = _read_gemini_settings(settings_file)
    if settings is None:
        return

    hooks_section: dict = settings["hooks"]
    added: list[str] = []
    skipped: list[str] = []

    for event in _GEMINI_HOOK_SCRIPTS:
        command = _managed_gemini_hook_command(claude_dir, event)
        event_hooks = hooks_section.setdefault(event, [])
        if not isinstance(event_hooks, list):
            _warn(f"Gemini hook event {event} is not a list; skipping")
            continue
        if _hook_already_registered(event_hooks, command):
            _print(dim(f"  Gemini hook {event} already registered"), verbose_only=True, verbose=verbose)
            skipped.append(event)
            continue
        new_entry = {
            "matcher": "*",
            "hooks": [
                {
                    "name": f"parsidion-{event.lower().replace('session', 'session-')}",
                    "type": "command",
                    "command": command,
                    "timeout": 10000,
                }
            ],
        }
        _step(f"Register Gemini hook {bold(event)}: {dim(command)}", dry_run=dry_run)
        if not dry_run:
            event_hooks.append(new_entry)
        added.append(event)

    if dry_run:
        return
    if added:
        try:
            settings_file.parent.mkdir(parents=True, exist_ok=True)
            settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif skipped:
        _ok("All Gemini hooks already registered")
```

Then add:

```python
def remove_gemini_hooks(
    gemini_home: Path,
    claude_dir: Path,
    dry_run: bool = False,
) -> bool:
    """Remove only Parsidion-managed Gemini hook commands from settings.json."""
    settings_file = _gemini_settings_file(gemini_home)
    settings = _read_gemini_settings(settings_file)
    if settings is None:
        return False
    if not settings_file.exists():
        _warn(f"Gemini settings.json not found: {settings_file}")
        return False

    hooks_section: dict = settings["hooks"]
    changed = False
    for event in _GEMINI_HOOK_SCRIPTS:
        command = _managed_gemini_hook_command(claude_dir, event)
        event_hooks = hooks_section.get(event, [])
        if not isinstance(event_hooks, list):
            continue
        filtered, event_changed = _filter_hook_entries(
            event_hooks,
            lambda hook, command=command: hook.get("command", "") == command,
        )
        if event_changed:
            _step(f"Remove Gemini hook {bold(event)}", dry_run=dry_run)
            changed = True
            if filtered:
                hooks_section[event] = filtered
            elif event in hooks_section:
                del hooks_section[event]

    if changed and not dry_run:
        try:
            settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
            _ok(f"Updated {settings_file}")
        except OSError as exc:
            _err(f"Could not write {settings_file}: {exc}")
    elif not changed:
        _warn("No Parsidion Gemini hook registrations found.")
    return changed
```

If ruff complains about the generated name expression, use explicit names:

```python
_GEMINI_HOOK_NAMES = {
    "SessionStart": "parsidion-session-start",
    "SessionEnd": "parsidion-session-end",
}
```

- [ ] **Step 6: Run GREEN tests**

```bash
uv run pytest tests/test_install.py::TestGeminiHooks -q
```

Expected: Gemini hook tests pass.

- [ ] **Step 7: Commit Task 2**

```bash
uv run ruff format install.py tests/test_install.py
uv run ruff check install.py tests/test_install.py
uv run pyright install.py tests/test_install.py
uv run pytest tests/test_install.py::TestGeminiHooks tests/test_install.py::TestParseArgs -q
git add install.py tests/test_install.py
git commit -m "feat: manage gemini hook registration"
```

---

### Task 3: Wire Gemini into install and uninstall flows

**Files:**
- Modify: `install.py`
- Modify: `tests/test_install.py`

- [ ] **Step 1: Write failing runtime-flow tests**

Add tests under `TestRuntimeFlow`:

```python
def test_runtime_gemini_dry_run_install_prints_gemini_plan(
    self, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
    vault = tmp_path / "ClaudeVault"
    claude_dir = tmp_path / ".claude"
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install.py",
            "--yes",
            "--runtime",
            "gemini",
            "--dry-run",
            "--vault",
            str(vault),
            "--claude-dir",
            str(claude_dir),
            "--gemini-home",
            str(gemini_home),
        ],
    )
    args = install.parse_args()

    result = install.install(args)

    output = capsys.readouterr().out
    assert result == 0
    assert "Runtime     : gemini" in output
    assert f"Gemini home : {gemini_home}" in output
    assert "Gemini hooks: SessionStart, SessionEnd" in output
    assert "Claude hooks:" not in output
    assert "Codex hooks :" not in output
    assert not (gemini_home / "settings.json").exists()


def test_runtime_all_dry_run_install_prints_all_runtime_plans(
    self, tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(install, "_FORBIDDEN_PREFIXES", ())
    vault = tmp_path / "ClaudeVault"
    claude_dir = tmp_path / ".claude"
    codex_home = tmp_path / ".codex"
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "install.py",
            "--yes",
            "--runtime",
            "all",
            "--dry-run",
            "--vault",
            str(vault),
            "--claude-dir",
            str(claude_dir),
            "--codex-home",
            str(codex_home),
            "--gemini-home",
            str(gemini_home),
        ],
    )
    args = install.parse_args()

    result = install.install(args)

    output = capsys.readouterr().out
    assert result == 0
    assert "Runtime     : all" in output
    assert "Claude hooks:" in output
    assert "Codex hooks : SessionStart, Stop" in output
    assert "Gemini hooks: SessionStart, SessionEnd" in output


def test_uninstall_gemini_runtime_removes_gemini_hooks_only(self, tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    settings_file = claude_dir / "settings.json"
    codex_home = tmp_path / ".codex"
    gemini_home = tmp_path / ".gemini"
    install.merge_codex_hooks(codex_home, claude_dir, dry_run=False, verbose=False)
    install.merge_gemini_hooks(gemini_home, claude_dir, dry_run=False, verbose=False)
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    codex_before = (codex_home / "hooks.json").read_text(encoding="utf-8")

    install.uninstall(
        claude_dir,
        settings_file,
        dry_run=False,
        yes=True,
        hooks_only=True,
        runtime="gemini",
        codex_home=codex_home,
        gemini_home=gemini_home,
    )

    gemini_settings = json.loads((gemini_home / "settings.json").read_text(encoding="utf-8"))
    assert gemini_settings["hooks"] == {}
    assert (codex_home / "hooks.json").read_text(encoding="utf-8") == codex_before
```

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest tests/test_install.py::TestRuntimeFlow -q
```

Expected: fails because `--gemini-home`, flow wiring, or uninstall signature does not exist.

- [ ] **Step 3: Add `--gemini-home` argument**

In `parse_args()`, add:

```python
parser.add_argument(
    "--gemini-home",
    default="~/.gemini",
    help="Gemini CLI home directory for hook settings (default: ~/.gemini)",
)
```

- [ ] **Step 4: Add runtime predicate helpers**

Near `resolve_runtime_choice()` or other helpers, add:

```python
def _wants_claude_runtime(runtime: str) -> bool:
    return runtime in {"claude", "both", "all"}


def _wants_codex_runtime(runtime: str) -> bool:
    return runtime in {"codex", "both", "all"}


def _wants_gemini_runtime(runtime: str) -> bool:
    return runtime in {"gemini", "all"}
```

Then replace existing checks such as `runtime in {"claude", "both"}` and `runtime in {"codex", "both"}` with these helpers.

- [ ] **Step 5: Wire install plan output and merge call**

In `install(args)`, resolve:

```python
gemini_home = Path(args.gemini_home).expanduser()
```

In the installation plan output:

```python
if _wants_gemini_runtime(runtime):
    print(f"  {dim('Gemini home :')} {gemini_home}")
    print(f"  {dim('Gemini hooks:')} SessionStart, SessionEnd")
```

In hook install phase:

```python
if not args.skip_hooks and _wants_gemini_runtime(runtime):
    merge_gemini_hooks(gemini_home, claude_dir, dry_run=args.dry_run, verbose=args.verbose)
```

Keep Claude skill installation as shared asset installation for all runtimes except `none`, because Gemini hook commands point to scripts under `~/.claude/skills/parsidion`.

- [ ] **Step 6: Wire uninstall signature and behavior**

Update `uninstall(...)` signature to include:

```python
gemini_home: Path | None = None,
```

Inside uninstall:

```python
gemini_home = gemini_home or (Path.home() / ".gemini")
if _wants_gemini_runtime(runtime):
    removed_hooks = remove_gemini_hooks(gemini_home, claude_dir, dry_run=dry_run) or removed_hooks
```

Update call sites from parsed args to pass `gemini_home`.

- [ ] **Step 7: Run GREEN runtime-flow tests**

```bash
uv run pytest tests/test_install.py::TestRuntimeFlow tests/test_install.py::TestGeminiHooks -q
```

Expected: pass.

- [ ] **Step 8: Commit Task 3**

```bash
uv run ruff format install.py tests/test_install.py
uv run ruff check install.py tests/test_install.py
uv run pyright install.py tests/test_install.py
uv run pytest tests/test_install.py -q
git add install.py tests/test_install.py
git commit -m "feat: wire gemini runtime installer flow"
```

---

### Task 4: Gemini transcript helpers

**Files:**
- Modify: `skills/parsidion/scripts/vault_hooks.py`
- Modify: `skills/parsidion/scripts/vault_common.py`
- Modify: `tests/test_vault_common.py`

- [ ] **Step 1: Write failing transcript helper tests**

Add after `TestCodexTranscriptHelpers` in `tests/test_vault_common.py`:

```python
class TestGeminiTranscriptHelpers:
    def test_allowed_transcript_roots_includes_gemini_roots(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        project = tmp_path / "project"
        home.mkdir()
        project.mkdir()
        monkeypatch.setenv("HOME", str(home))

        roots = vault_common.allowed_transcript_roots(cwd=str(project))

        assert (home / ".gemini").resolve() in roots
        assert (project / ".gemini").resolve() in roots

    def test_is_gemini_transcript_path_accepts_user_and_project_roots(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        project = tmp_path / "project"
        user_transcript = home / ".gemini" / "tmp" / "session.jsonl"
        project_transcript = project / ".gemini" / "tmp" / "session.jsonl"
        user_transcript.parent.mkdir(parents=True)
        project_transcript.parent.mkdir(parents=True)
        user_transcript.write_text("", encoding="utf-8")
        project_transcript.write_text("", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))

        assert vault_common.is_gemini_transcript_path(user_transcript, cwd=str(project))
        assert vault_common.is_gemini_transcript_path(project_transcript, cwd=str(project))
        assert vault_common.is_allowed_transcript_path(user_transcript, cwd=str(project))
        assert vault_common.is_allowed_transcript_path(project_transcript, cwd=str(project))

    def test_parse_gemini_transcript_lines_extracts_model_text(self) -> None:
        lines = [
            '{"role":"model","content":"Fixed the parser bug"}',
            '{"role":"user","content":"hello"}',
            '{"type":"assistant","content":[{"type":"text","text":"Root cause was config"}]}',
            "not json",
        ]

        assert vault_common.parse_gemini_transcript_lines(lines) == [
            "Fixed the parser bug",
            "Root cause was config",
        ]

    def test_parse_gemini_transcript_lines_extracts_message_wrapper_and_llm_response(self) -> None:
        lines = [
            '{"message":{"role":"model","content":[{"type":"text","text":"Pattern was useful"}]}}',
            json.dumps(
                {
                    "llm_response": {
                        "candidates": [
                            {
                                "content": {
                                    "role": "model",
                                    "parts": ["First part", {"text": "Second part"}],
                                }
                            }
                        ]
                    }
                }
            ),
        ]

        assert vault_common.parse_gemini_transcript_lines(lines) == [
            "Pattern was useful",
            "First part\nSecond part",
        ]
```

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest tests/test_vault_common.py::TestGeminiTranscriptHelpers -q
```

Expected: fails because helpers are missing.

- [ ] **Step 3: Add helper names to `vault_hooks.__all__`**

In `skills/parsidion/scripts/vault_hooks.py`, update `__all__` to include:

```python
"gemini_home",
"is_gemini_transcript_path",
"parse_gemini_transcript_lines",
```

- [ ] **Step 4: Implement Gemini root helpers**

Add near `codex_home()`:

```python
def gemini_home() -> Path:
    """Return the configured Gemini CLI home directory."""
    return Path(os.environ.get("GEMINI_HOME", "~/.gemini")).expanduser().resolve()
```

Update `allowed_transcript_roots(cwd)` docstring and roots list:

```python
roots: list[Path] = [
    Path.home() / ".claude",
    Path.home() / ".pi",
    codex_home() / "sessions",
    gemini_home(),
]
```

When `cwd` is provided, also append:

```python
roots.append(Path(cwd).resolve() / ".gemini")
```

Add:

```python
def is_gemini_transcript_path(transcript_path: Path, cwd: str | None = None) -> bool:
    """Return True when *transcript_path* belongs to a Gemini transcript root."""
    try:
        resolved = transcript_path.expanduser().resolve()
    except OSError:
        return False

    roots: list[Path] = [gemini_home()]
    if cwd:
        try:
            roots.append(Path(cwd).resolve() / ".gemini")
        except OSError:
            pass

    for root in roots:
        try:
            root_resolved = root.resolve()
            if resolved == root_resolved or resolved.is_relative_to(root_resolved):
                return True
        except (ValueError, OSError):
            continue
    return False
```

- [ ] **Step 5: Implement parser helpers**

Add private helpers near `parse_codex_transcript_lines()`:

```python
def _extract_gemini_parts(parts: object) -> str:
    if isinstance(parts, str):
        return parts.strip()
    if not isinstance(parts, list):
        return ""
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, str) and part.strip():
            chunks.append(part.strip())
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
    return "\n".join(chunks)


def _extract_gemini_content(content: object) -> str:
    text = extract_text_from_content(content).strip()
    if text:
        return text
    return _extract_gemini_parts(content)
```

Add parser:

```python
def parse_gemini_transcript_lines(lines: list[str]) -> list[str]:
    """Parse Gemini transcript JSONL lines and extract model/assistant text."""
    texts: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue

        message = record.get("message")
        if isinstance(message, dict):
            role = message.get("role")
            if role in {"model", "assistant"}:
                text = _extract_gemini_content(message.get("content"))
                if text:
                    texts.append(text)
                continue

        role = record.get("role")
        record_type = record.get("type")
        if role in {"model", "assistant"} or record_type in {"model", "assistant"}:
            text = _extract_gemini_content(record.get("content"))
            if text:
                texts.append(text)
            continue

        llm_response = record.get("llm_response")
        if isinstance(llm_response, dict):
            candidates = llm_response.get("candidates")
            if not isinstance(candidates, list):
                continue
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                if content.get("role") not in {"model", "assistant", None}:
                    continue
                text = _extract_gemini_parts(content.get("parts"))
                if text:
                    texts.append(text)
    return texts
```

- [ ] **Step 6: Re-export from `vault_common.py`**

Add imported names in the `from vault_hooks import ...` block:

```python
gemini_home,
is_gemini_transcript_path,
parse_gemini_transcript_lines,
```

Add them to `__all__`.

- [ ] **Step 7: Run GREEN tests**

```bash
uv run pytest tests/test_vault_common.py::TestGeminiTranscriptHelpers -q
```

Expected: pass.

- [ ] **Step 8: Commit Task 4**

```bash
uv run ruff format skills/parsidion/scripts/vault_hooks.py skills/parsidion/scripts/vault_common.py tests/test_vault_common.py
uv run ruff check skills/parsidion/scripts/vault_hooks.py skills/parsidion/scripts/vault_common.py tests/test_vault_common.py
uv run pyright skills/parsidion/scripts/vault_hooks.py skills/parsidion/scripts/vault_common.py tests/test_vault_common.py
uv run pytest tests/test_vault_common.py::TestGeminiTranscriptHelpers tests/test_vault_common.py::TestCodexTranscriptHelpers -q
git add skills/parsidion/scripts/vault_hooks.py skills/parsidion/scripts/vault_common.py tests/test_vault_common.py
git commit -m "feat: parse gemini transcripts"
```

---

### Task 5: Gemini hook wrapper scripts

**Files:**
- Create: `skills/parsidion/scripts/gemini_session_start_hook.py`
- Create: `skills/parsidion/scripts/gemini_session_end_hook.py`
- Modify: `tests/test_hook_integration.py`

- [ ] **Step 1: Write failing hook integration tests**

Add a new class to `tests/test_hook_integration.py` after `TestCodexHookIntegration`:

```python
@pytest.mark.timeout(15)
class TestGeminiHookIntegration:
    def test_gemini_session_start_sets_runtime_hint_and_outputs_context(
        self, tmp_path: Path
    ) -> None:
        result = _run_hook(
            "gemini_session_start_hook.py",
            {"cwd": str(tmp_path), "hook_event_name": "SessionStart", "source": "startup"},
            tmp_path,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert "additionalContext" in parsed["hookSpecificOutput"]

    def test_gemini_session_end_missing_transcript_exits_cleanly(self, tmp_path: Path) -> None:
        result = _run_hook(
            "gemini_session_end_hook.py",
            {"cwd": str(tmp_path), "transcript_path": "/missing/session.jsonl"},
            tmp_path,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_gemini_session_end_with_real_transcript_queues_pending(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        transcript = project / ".gemini" / "sessions" / "gemini-session.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            json.dumps(
                {
                    "role": "model",
                    "content": "Root cause was a missing environment variable.",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = _run_hook(
            "gemini_session_end_hook.py",
            {
                "cwd": str(project),
                "transcript_path": str(transcript),
                "session_id": "gemini-session",
                "reason": "exit",
            },
            tmp_path,
            extra_env={"HOME": str(tmp_path)},
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["transcript_path"] == str(transcript)
        assert "error_fix" in entry["categories"]

    def test_gemini_session_end_skips_internal_sessions(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        transcript = project / ".gemini" / "sessions" / "gemini-session.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text(
            '{"role":"model","content":"Root cause was a missing env var."}\n',
            encoding="utf-8",
        )

        result = _run_hook(
            "gemini_session_end_hook.py",
            {"cwd": str(project), "transcript_path": str(transcript)},
            tmp_path,
            extra_env={"HOME": str(tmp_path), "PARSIDION_INTERNAL": "1"},
        )

        assert result.returncode == 0
        assert json.loads(result.stdout) == {}
        assert not (tmp_path / "pending_summaries.jsonl").exists()
```

- [ ] **Step 2: Run RED tests**

```bash
uv run pytest tests/test_hook_integration.py::TestGeminiHookIntegration -q
```

Expected: scripts not found.

- [ ] **Step 3: Create `gemini_session_start_hook.py`**

Create `skills/parsidion/scripts/gemini_session_start_hook.py`:

```python
#!/usr/bin/env python3
"""Gemini SessionStart hook wrapper for Parsidion vault context."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import vault_common
from session_start_hook import _DEFAULT_MAX_CHARS, build_session_context


def _read_payload() -> dict[str, object]:
    """Read a JSON object from stdin, returning an empty payload on bad input."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    """Build Gemini additional context and write a JSON hook response."""
    try:
        payload = _read_payload()
        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())
        max_chars = int(
            vault_common.get_config("session_start_hook", "max_chars", _DEFAULT_MAX_CHARS)
        )
        old_runtime = os.environ.get("PARSIDION_RUNTIME")
        os.environ["PARSIDION_RUNTIME"] = "gemini"
        try:
            context, _notes_injected = build_session_context(
                cwd,
                ai_model=None,
                max_chars=max_chars,
                verbose_mode=False,
            )
        finally:
            if old_runtime is None:
                os.environ.pop("PARSIDION_RUNTIME", None)
            else:
                os.environ["PARSIDION_RUNTIME"] = old_runtime
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": context,
                    }
                }
            )
        )
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `gemini_session_end_hook.py`**

Create `skills/parsidion/scripts/gemini_session_end_hook.py`:

```python
#!/usr/bin/env python3
"""Gemini SessionEnd hook wrapper for Parsidion transcript queueing."""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

import vault_common

_DEFAULT_TRANSCRIPT_TAIL_LINES = 200


def _read_payload() -> dict[str, object]:
    """Read a JSON object from stdin, returning an empty payload on bad input."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_summary(texts: list[str]) -> str:
    """Return a compact summary candidate from parsed model text."""
    for text in texts:
        if len(text.strip()) > 50:
            return text[:500]
    return texts[0][:500] if texts else ""


def main() -> None:
    """Process a Gemini transcript and queue useful session summaries."""
    try:
        payload = _read_payload()
        if os.environ.get("PARSIDION_INTERNAL"):
            sys.stdout.write("{}")
            return

        cwd_value = payload.get("cwd")
        cwd = str(cwd_value) if cwd_value else str(Path.cwd())
        transcript_value = payload.get("transcript_path")
        if not transcript_value:
            sys.stdout.write("{}")
            return

        transcript_path = Path(str(transcript_value))
        if not transcript_path.is_file():
            sys.stdout.write("{}")
            return
        if not vault_common.is_allowed_transcript_path(transcript_path, cwd=cwd):
            sys.stdout.write("{}")
            return
        if not vault_common.is_gemini_transcript_path(transcript_path, cwd=cwd):
            sys.stdout.write("{}")
            return

        vault_path = vault_common.resolve_vault(cwd=cwd)
        vault_common.ensure_vault_dirs(vault=vault_path)
        tail_lines = int(
            vault_common.get_config(
                "session_stop_hook",
                "transcript_tail_lines",
                _DEFAULT_TRANSCRIPT_TAIL_LINES,
            )
        )
        raw_lines = vault_common.read_last_n_lines(transcript_path, tail_lines)
        model_texts = vault_common.parse_gemini_transcript_lines(raw_lines)
        if not model_texts:
            sys.stdout.write("{}")
            return

        categories = vault_common.detect_categories(model_texts)
        if categories:
            project = vault_common.get_project_name(cwd)
            vault_common.append_session_to_daily(
                project,
                categories,
                _first_summary(model_texts),
                vault_path,
            )
            vault_common.append_to_pending(
                transcript_path,
                project,
                categories,
                vault=vault_path,
            )
        sys.stdout.write("{}")
    except Exception:  # noqa: BLE001 - hooks must not fail closed
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run GREEN hook tests**

```bash
uv run pytest tests/test_hook_integration.py::TestGeminiHookIntegration -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 5**

```bash
chmod +x skills/parsidion/scripts/gemini_session_start_hook.py skills/parsidion/scripts/gemini_session_end_hook.py
uv run ruff format skills/parsidion/scripts/gemini_session_start_hook.py skills/parsidion/scripts/gemini_session_end_hook.py tests/test_hook_integration.py
uv run ruff check skills/parsidion/scripts/gemini_session_start_hook.py skills/parsidion/scripts/gemini_session_end_hook.py tests/test_hook_integration.py
uv run pyright skills/parsidion/scripts/gemini_session_start_hook.py skills/parsidion/scripts/gemini_session_end_hook.py tests/test_hook_integration.py
uv run pytest tests/test_hook_integration.py::TestGeminiHookIntegration -q
git add skills/parsidion/scripts/gemini_session_start_hook.py skills/parsidion/scripts/gemini_session_end_hook.py tests/test_hook_integration.py
git commit -m "feat: add gemini runtime hooks"
```

---

### Task 6: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `skills/parsidion/SKILL.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `CHANGELOG.md`
- Modify only code/tests needed for verification fixes.

- [ ] **Step 1: Update docs**

Update docs to mention:

- Gemini runtime hooks are supported for `SessionStart` and `SessionEnd`.
- Install with `--runtime gemini` or `--runtime all`.
- Gemini settings are written to `~/.gemini/settings.json`.
- Gemini runtime hooks are separate from prompt AI backend selection.
- Gemini subagent lifecycle capture is not native in this first pass.

Add `CHANGELOG.md` entry:

```markdown
- **Gemini runtime hooks** — Added installer support for Gemini CLI `SessionStart` and `SessionEnd` hooks, Gemini transcript parsing, and `--runtime gemini` / `--runtime all` runtime selection.
```

- [ ] **Step 2: Run docs grep checks**

```bash
rg 'Gemini runtime|--runtime gemini|--runtime all|~/.gemini/settings.json|SessionStart.*SessionEnd' README.md skills/parsidion/SKILL.md docs/ARCHITECTURE.md CHANGELOG.md
```

Expected: relevant matches are present.

- [ ] **Step 3: Run targeted tests**

```bash
uv run pytest tests/test_install.py tests/test_hook_integration.py tests/test_vault_common.py -q
```

Expected: all pass.

- [ ] **Step 4: Run installer dry-run smoke checks**

```bash
uv run python install.py --yes --runtime gemini --dry-run --vault /tmp/parsidion-vault-smoke
uv run python install.py --yes --runtime all --dry-run --vault /tmp/parsidion-vault-smoke
```

Expected:

- Gemini dry run mentions `Runtime     : gemini`, `Gemini home`, and `Gemini hooks: SessionStart, SessionEnd`.
- All dry run mentions Claude hooks, Codex hooks, and Gemini hooks.
- Neither command writes to real user settings because of `--dry-run`.

- [ ] **Step 5: Commit docs**

```bash
uv run ruff format .
uv run ruff check .
uv run pyright .
uv run pytest tests/test_install.py tests/test_hook_integration.py tests/test_vault_common.py -q
git add README.md skills/parsidion/SKILL.md docs/ARCHITECTURE.md CHANGELOG.md
git commit -m "docs: document gemini runtime integration"
```

If formatting/type/test fixes touched additional files, include only those related files in the commit.

- [ ] **Step 6: Run full verification**

```bash
make checkall
```

Expected:

- ruff format leaves files unchanged or formats cleanly.
- ruff check passes.
- pyright reports `0 errors`.
- pytest passes.

- [ ] **Step 7: Final review**

Request review of the feature branch with focus on:

- Gemini settings merge/remove preserving unrelated hooks.
- Runtime `both` compatibility and `all` semantics.
- Gemini transcript parser safety and permissiveness.
- Hook stdout always valid JSON.
- No regression to Claude/Codex behavior.

- [ ] **Step 8: Commit verification fixes if needed**

If final verification or review requires changes:

```bash
git add <changed-files>
git commit -m "fix: finalize gemini runtime hooks"
make checkall
```

If no fixes are needed, do not create an empty commit.

- [ ] **Step 9: Final status**

```bash
git status --short --branch
git log --oneline --max-count=10
```

Expected: clean worktree on `feature/gemini-runtime-hooks`.
