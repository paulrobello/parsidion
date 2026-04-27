# Codex CLI AI Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backend-neutral prompt AI layer so existing `claude -p` scripts can use `codex exec` when configured or when Codex runtime is detected.

**Architecture:** Introduce a focused `ai_backend.py` helper that resolves backend, resolves backend-specific model defaults, and runs either Claude CLI or Codex CLI. Migrate direct prompt-style `claude -p` call sites to the helper while leaving `summarize_sessions.py`/`claude-agent-sdk` untouched.

**Tech Stack:** Python 3.13, stdlib subprocess/tempfile/pathlib, existing Parsidion config helpers, pytest/monkeypatch, ruff, pyright.

---

## File Structure

- Create `skills/parsidion/scripts/ai_backend.py`
  - Backend resolution: `auto | claude-cli | codex-cli | none`
  - Backend-specific model resolution: `small | large`
  - Safe subprocess wrappers for Claude CLI and Codex CLI
- Modify `skills/parsidion/scripts/vault_config.py`
  - Add schema entries for `ai`, `ai_models`, and `codex_cli`
- Modify `skills/parsidion/scripts/session_start_hook.py`
  - Replace direct `claude -p` AI selection subprocess with `ai_backend.run_ai_prompt(..., model_tier="small")`
  - Preserve single-flight/cooldown/timeout behavior
- Modify `skills/parsidion/scripts/session_stop_hook.py`
  - Replace direct `claude -p` classification subprocess with `ai_backend.run_ai_prompt(..., model_tier="small")`
- Modify `skills/parsidion/scripts/vault_doctor.py`
  - Replace two direct `claude -p` helpers with `ai_backend.run_ai_prompt(..., model_tier="small")`
- Modify `skills/parsidion/scripts/vault_merge.py`
  - Replace merge synthesis `claude -p` with `ai_backend.run_ai_prompt(..., model_tier="large")`
- Modify `skills/parsidion/scripts/run_trigger_eval.py`
  - Replace direct `claude -p` with helper, `model_tier="small"`
- Modify `skills/parsidion/scripts/embed_eval_generate.py`
  - Replace direct `claude -p` with helper, `model_tier="small"`
- Modify `skills/parsidion/scripts/codex_session_start_hook.py`
  - Set `PARSIDION_RUNTIME=codex` while building context so `auto` can select Codex if AI is enabled later
- Create `tests/test_ai_backend.py`
  - Unit tests for backend/model resolution and command construction
- Modify existing tests as needed:
  - `tests/test_session_start_hook.py`
  - `tests/test_session_stop_hook.py`
  - `tests/test_vault_doctor.py`
  - `tests/test_hook_integration.py`
- Update docs:
  - `README.md`
  - `skills/parsidion/SKILL.md`
  - `CHANGELOG.md`

---

### Task 1: Config schema and AI backend helper

**Files:**
- Create: `skills/parsidion/scripts/ai_backend.py`
- Modify: `skills/parsidion/scripts/vault_config.py`
- Test: `tests/test_ai_backend.py`

- [ ] **Step 1: Write failing backend resolution tests**

Create `tests/test_ai_backend.py` with these initial tests. Use direct import by adding the scripts directory to `sys.path`, matching existing test style.

```python
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import ai_backend  # noqa: E402
import vault_common  # noqa: E402


def _reset_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_text: str = "") -> Path:
    config = tmp_path / "config.yaml"
    config.write_text(config_text, encoding="utf-8")
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))
    vault_common._resolve_vault_cached.cache_clear()
    vault_common._clear_config_cache()
    return tmp_path


class TestResolveAiBackend:
    def test_auto_uses_codex_runtime_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "codex")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_auto_uses_claude_runtime_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.setenv("PARSIDION_RUNTIME", "claude")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_auto_uses_claudecode_when_no_parsidion_hint(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.delenv("PARSIDION_RUNTIME", raising=False)
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_auto_defaults_to_claude_when_ambiguous(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: auto\n")
        monkeypatch.delenv("PARSIDION_RUNTIME", raising=False)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.delenv("CODEX_SANDBOX", raising=False)

        assert ai_backend.resolve_ai_backend(vault=vault) == "claude-cli"

    def test_explicit_codex_backend_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        monkeypatch.setenv("CLAUDECODE", "1")

        assert ai_backend.resolve_ai_backend(vault=vault) == "codex-cli"

    def test_none_backend_disables_ai(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: none\n")

        assert ai_backend.resolve_ai_backend(vault=vault) == "none"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd /Users/probello/Repos/parsidion/.worktrees/codex-ai-backend
uv run pytest tests/test_ai_backend.py -q
```

Expected: fail during import with `ModuleNotFoundError: No module named 'ai_backend'`.

- [ ] **Step 3: Add config schema entries and minimal helper**

Modify `skills/parsidion/scripts/vault_config.py` inside `_CONFIG_SCHEMA` to include:

```python
    "ai": {
        "backend": (str,),
    },
    "ai_models": {
        "claude": (dict,),
        "codex": (dict,),
    },
    "codex_cli": {
        "command": (str,),
        "timeout": (int, float),
        "sandbox": (str, type(None)),
        "ephemeral": (bool,),
        "skip_git_repo_check": (bool,),
    },
```

Create `skills/parsidion/scripts/ai_backend.py` with:

```python
#!/usr/bin/env python3
"""Backend-neutral prompt AI helpers for Parsidion scripts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal

import vault_common

AiBackend = Literal["claude-cli", "codex-cli", "none"]
ModelTier = Literal["small", "large"]

_DEFAULT_BACKEND = "auto"
_DEFAULT_CLAUDE_MODELS: dict[ModelTier, str] = {
    "small": "claude-haiku-4-5-20251001",
    "large": "claude-sonnet-4-6",
}
_DEFAULT_CODEX_MODELS: dict[ModelTier, str] = {
    "small": "gpt-5.5",
    "large": "gpt-5.5",
}
_DEFAULT_CODEX_TIMEOUT = 60
_DEFAULT_CLAUDE_TIMEOUT = 30


def _configured_backend(vault: Path | None = None) -> str:
    value = vault_common.get_config("ai", "backend", _DEFAULT_BACKEND, vault=vault)
    return str(value).strip().lower() if value is not None else _DEFAULT_BACKEND


def resolve_ai_backend(vault: Path | None = None) -> AiBackend:
    """Resolve the configured prompt AI backend."""
    backend = _configured_backend(vault=vault)
    if backend in {"claude-cli", "codex-cli", "none"}:
        return backend  # type: ignore[return-value]
    if backend != "auto":
        return "claude-cli"

    runtime_hint = os.environ.get("PARSIDION_RUNTIME", "").strip().lower()
    if runtime_hint == "codex":
        return "codex-cli"
    if runtime_hint == "claude":
        return "claude-cli"

    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_SESSION_ID"):
        return "codex-cli"
    if os.environ.get("CLAUDECODE"):
        return "claude-cli"
    return "claude-cli"


def _model_from_config(
    backend_key: str,
    model_tier: ModelTier,
    defaults: dict[ModelTier, str],
    vault: Path | None,
) -> str:
    section = vault_common.get_config("ai_models", backend_key, None, vault=vault)
    if isinstance(section, dict):
        value = section.get(model_tier)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return defaults[model_tier]


def resolve_ai_model(
    backend: AiBackend,
    *,
    model: str | None = None,
    model_tier: ModelTier = "small",
    vault: Path | None = None,
) -> str | None:
    """Resolve an explicit or backend-specific default model."""
    if model is not None and model.strip():
        return model.strip()
    if backend == "none":
        return None
    if backend == "codex-cli":
        return _model_from_config("codex", model_tier, _DEFAULT_CODEX_MODELS, vault)
    return _model_from_config("claude", model_tier, _DEFAULT_CLAUDE_MODELS, vault)


def _codex_env(vault: Path | None = None) -> dict[str, str]:
    """Return a filtered environment for Codex CLI subprocesses."""
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "CODEX_HOME",
        "PARSIDION_RUNTIME",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    env["PARSIDION_INTERNAL"] = "1"
    return env


def _run_claude_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
) -> str | None:
    cmd = ["claude", "-p", prompt]
    if model:
        cmd.extend(["--model", model])
    cmd.append("--no-session-persistence")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout or _DEFAULT_CLAUDE_TIMEOUT,
            cwd=str(cwd) if cwd is not None else None,
            env=vault_common.env_without_claudecode(vault=vault),
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    output = result.stdout.strip()
    return output or None


def _run_codex_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
) -> str | None:
    command = str(vault_common.get_config("codex_cli", "command", "codex", vault=vault))
    codex_timeout = timeout or vault_common.get_config(
        "codex_cli", "timeout", _DEFAULT_CODEX_TIMEOUT, vault=vault
    )
    sandbox = vault_common.get_config("codex_cli", "sandbox", "read-only", vault=vault)
    ephemeral = bool(vault_common.get_config("codex_cli", "ephemeral", True, vault=vault))
    skip_git = bool(
        vault_common.get_config("codex_cli", "skip_git_repo_check", True, vault=vault)
    )

    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="parsidion-codex-", delete=False
        ) as output_file:
            output_path = Path(output_file.name)

        cmd = [command, "exec"]
        if ephemeral:
            cmd.append("--ephemeral")
        if sandbox:
            cmd.extend(["--sandbox", str(sandbox)])
        if skip_git:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["--output-last-message", str(output_path)])
        if model:
            cmd.extend(["--model", model])
        cmd.append(prompt)

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=codex_timeout,
            cwd=str(cwd) if cwd is not None else None,
            env=_codex_env(vault=vault),
        )
        if result.returncode != 0:
            return None
        output = output_path.read_text(encoding="utf-8").strip()
        return output or None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None
    finally:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_ai_prompt(
    prompt: str,
    *,
    model: str | None = None,
    model_tier: ModelTier = "small",
    timeout: int | float | None = None,
    cwd: str | Path | None = None,
    purpose: str = "general",
    vault: Path | None = None,
) -> str | None:
    """Run *prompt* through the configured prompt AI backend."""
    del purpose
    backend = resolve_ai_backend(vault=vault)
    if backend == "none":
        return None
    resolved_model = resolve_ai_model(
        backend, model=model, model_tier=model_tier, vault=vault
    )
    if backend == "codex-cli":
        return _run_codex_prompt(
            prompt, model=resolved_model, timeout=timeout, cwd=cwd, vault=vault
        )
    return _run_claude_prompt(
        prompt, model=resolved_model, timeout=timeout, cwd=cwd, vault=vault
    )
```

- [ ] **Step 4: Run backend resolution tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_ai_backend.py -q
```

Expected: backend resolution tests pass.

- [ ] **Step 5: Add failing model and command tests**

Append these tests to `tests/test_ai_backend.py`:

```python
class TestResolveAiModel:
    def test_codex_defaults_use_gpt_5_5(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert ai_backend.resolve_ai_model("codex-cli", model_tier="small", vault=vault) == "gpt-5.5"
        assert ai_backend.resolve_ai_model("codex-cli", model_tier="large", vault=vault) == "gpt-5.5"

    def test_claude_defaults_are_tiered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert ai_backend.resolve_ai_model("claude-cli", model_tier="small", vault=vault) == "claude-haiku-4-5-20251001"
        assert ai_backend.resolve_ai_model("claude-cli", model_tier="large", vault=vault) == "claude-sonnet-4-6"

    def test_configured_codex_models_override_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(
            monkeypatch,
            tmp_path,
            "ai_models:\n  codex:\n    small: gpt-5.5-mini\n    large: gpt-5.5\n",
        )

        assert ai_backend.resolve_ai_model("codex-cli", model_tier="small", vault=vault) == "gpt-5.5-mini"
        assert ai_backend.resolve_ai_model("codex-cli", model_tier="large", vault=vault) == "gpt-5.5"

    def test_explicit_model_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path)

        assert ai_backend.resolve_ai_model("codex-cli", model="custom-model", model_tier="large", vault=vault) == "custom-model"


class TestRunAiPrompt:
    def test_claude_command_uses_resolved_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: claude-cli\n")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="answer\n", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ai_backend.run_ai_prompt("hello", model_tier="small", vault=vault)

        assert result == "answer"
        assert calls == [["claude", "-p", "hello", "--model", "claude-haiku-4-5-20251001", "--no-session-persistence"]]

    def test_codex_command_reads_output_last_message(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("codex answer\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="stream noise", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        result = ai_backend.run_ai_prompt("hello", model_tier="large", vault=vault)

        assert result == "codex answer"
        cmd = calls[0]
        assert cmd[:2] == ["codex", "exec"]
        assert "--ephemeral" in cmd
        assert cmd[cmd.index("--sandbox") + 1] == "read-only"
        assert "--skip-git-repo-check" in cmd
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
        assert cmd[-1] == "hello"

    def test_codex_failure_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        vault = _reset_config(monkeypatch, tmp_path, "ai:\n  backend: codex-cli\n")

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="failed")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert ai_backend.run_ai_prompt("hello", vault=vault) is None
```

- [ ] **Step 6: Run tests to verify RED for command/model behavior**

Run:

```bash
uv run pytest tests/test_ai_backend.py -q
```

Expected: these tests may already pass if Step 3 implementation included all behavior. If any test passes immediately, confirm it is testing behavior implemented in Step 3; do not change production code only to force a failure.

- [ ] **Step 7: Complete helper implementation until all backend tests pass**

If failures remain, update `ai_backend.py` only. Preserve the public API from the spec. Do not add SDK or OpenAI API support.

- [ ] **Step 8: Run focused verification**

Run:

```bash
uv run pytest tests/test_ai_backend.py tests/test_vault_common.py -q
uv run ruff format skills/parsidion/scripts/ai_backend.py tests/test_ai_backend.py skills/parsidion/scripts/vault_config.py
uv run ruff check skills/parsidion/scripts/ai_backend.py tests/test_ai_backend.py skills/parsidion/scripts/vault_config.py
uv run pyright skills/parsidion/scripts/ai_backend.py
```

Expected: tests pass, formatting clean, ruff clean, pyright 0 errors for new helper.

- [ ] **Step 9: Commit Task 1**

```bash
git add skills/parsidion/scripts/ai_backend.py skills/parsidion/scripts/vault_config.py tests/test_ai_backend.py
git commit -m "feat: add prompt ai backend helper"
```

---

### Task 2: Migrate SessionStart and SessionStop AI paths

**Files:**
- Modify: `skills/parsidion/scripts/session_start_hook.py`
- Modify: `skills/parsidion/scripts/session_stop_hook.py`
- Modify: `skills/parsidion/scripts/codex_session_start_hook.py`
- Test: `tests/test_session_start_hook.py`
- Test: `tests/test_session_stop_hook.py`
- Test: `tests/test_hook_integration.py`

- [ ] **Step 1: Write failing SessionStop helper test**

In `tests/test_session_stop_hook.py`, add a test near AI/classification tests or create a new class if none exists:

```python
def test_classify_session_with_ai_uses_small_tier_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return '{"should_queue": true, "categories": ["research"], "summary": "Found docs."}'

    monkeypatch.setattr(session_stop_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result = session_stop_hook._classify_session_with_ai(
        ["I researched the Codex CLI non-interactive mode and found codex exec."],
        "parsidion",
        None,
    )

    assert result == {
        "should_queue": True,
        "categories": ["research"],
        "summary": "Found docs.",
    }
    assert calls
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "session-stop-classification"
```

If `_classify_session_with_ai` currently requires `model: str`, update the test expectation to call it with `None`; the implementation step changes its signature to `model: str | None`.

- [ ] **Step 2: Write failing SessionStart helper test**

In `tests/test_session_start_hook.py`, add:

```python
def test_select_context_with_ai_uses_small_tier_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = tmp_path / "Patterns" / "codex-exec.md"
    note.parent.mkdir(parents=True)
    note.write_text(
        "---\ntags: [codex]\n---\n# Codex Exec\nUse codex exec for non-interactive prompts.\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "### Codex Exec\nUse codex exec for non-interactive prompts."

    monkeypatch.setattr(session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt)
    monkeypatch.setattr(session_start_hook, "_try_acquire_ai_lock", lambda vault_path: None)

    context = session_start_hook._select_context_with_ai(
        "parsidion",
        str(tmp_path),
        [note],
        None,
        4000,
        vault_path=tmp_path,
    )

    assert "Codex Exec" in context
    assert calls
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "session-start-selection"
```

Use existing test helpers in `test_session_start_hook.py` if they already patch locks/cooldown differently. The intent is to verify helper usage and tier, not lock behavior.

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_session_stop_hook.py::test_classify_session_with_ai_uses_small_tier_backend tests/test_session_start_hook.py::test_select_context_with_ai_uses_small_tier_backend -q
```

Expected: fail because `session_stop_hook` and `session_start_hook` do not import `ai_backend`, or because helper is not called.

- [ ] **Step 4: Migrate `session_stop_hook.py`**

Add import:

```python
import ai_backend
```

Change `_classify_session_with_ai` signature:

```python
def _classify_session_with_ai(
    assistant_texts: list[str],
    project: str,
    model: str | None,
) -> dict[str, object] | None:
```

Replace the `subprocess.run(["claude", ...])` block with:

```python
    output = ai_backend.run_ai_prompt(
        prompt,
        model=model,
        model_tier="small",
        timeout=vault_common.get_config(
            "session_stop_hook", "ai_timeout", _DEFAULT_AI_TIMEOUT
        ),
        purpose="session-stop-classification",
    )
    if not output:
        return None
```

Keep the existing JSON fence stripping and parsing logic after this block unchanged.

When resolving model in `main`, preserve explicit config but avoid hardcoding Claude defaults:

```python
        ai_model: str | None = args.ai
        if ai_model is None:
            ai_model = vault_common.get_config("session_stop_hook", "ai_model")
```

This already returns `None` by default, so no further change is needed unless the file has a Claude default fallback.

- [ ] **Step 5: Migrate `session_start_hook.py`**

Add import:

```python
import ai_backend
```

Change `_select_context_with_ai` signature to accept optional model:

```python
def _select_context_with_ai(
    project_name: str,
    cwd: str,
    candidate_notes: list[Path],
    model: str | None,
    max_chars: int = _DEFAULT_MAX_CHARS,
    vault_path: Path | None = None,
) -> str:
```

Replace the `subprocess.Popen(["claude", ...])` AI call with:

```python
        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=vault_common.get_config(
                "session_start_hook", "ai_timeout", _DEFAULT_AI_TIMEOUT
            ),
            cwd=cwd,
            purpose="session-start-selection",
            vault=vault_path,
        )
        if output:
            _write_ai_cooldown_stamp(vault_path)
            return output.strip()
```

Keep the lock acquisition, cooldown, timeout cleanup helper definitions, and fallback return behavior. `_kill_process_group` may become unused after removing `Popen`; remove only if ruff reports it as unused and no tests use it.

- [ ] **Step 6: Migrate `codex_session_start_hook.py` runtime hint**

Set a Codex runtime hint around context building without overriding an existing explicit value permanently:

```python
        old_runtime = os.environ.get("PARSIDION_RUNTIME")
        os.environ["PARSIDION_RUNTIME"] = "codex"
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
```

Add `import os`.

- [ ] **Step 7: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_session_start_hook.py tests/test_session_stop_hook.py tests/test_hook_integration.py::TestCodexHookIntegration -q
```

Expected: all selected tests pass.

- [ ] **Step 8: Format and lint touched files**

Run:

```bash
uv run ruff format skills/parsidion/scripts/session_start_hook.py skills/parsidion/scripts/session_stop_hook.py skills/parsidion/scripts/codex_session_start_hook.py tests/test_session_start_hook.py tests/test_session_stop_hook.py
uv run ruff check skills/parsidion/scripts/session_start_hook.py skills/parsidion/scripts/session_stop_hook.py skills/parsidion/scripts/codex_session_start_hook.py tests/test_session_start_hook.py tests/test_session_stop_hook.py
```

Expected: formatting complete and ruff clean.

- [ ] **Step 9: Commit Task 2**

```bash
git add skills/parsidion/scripts/session_start_hook.py skills/parsidion/scripts/session_stop_hook.py skills/parsidion/scripts/codex_session_start_hook.py tests/test_session_start_hook.py tests/test_session_stop_hook.py
git commit -m "feat: route hook ai calls through backend helper"
```

---

### Task 3: Migrate CLI utility AI calls

**Files:**
- Modify: `skills/parsidion/scripts/vault_doctor.py`
- Modify: `skills/parsidion/scripts/vault_merge.py`
- Modify: `skills/parsidion/scripts/run_trigger_eval.py`
- Modify: `skills/parsidion/scripts/embed_eval_generate.py`
- Test: `tests/test_vault_doctor.py`
- Test: `tests/test_embed_eval.py`
- Optional new test file: `tests/test_ai_script_migration.py`

- [ ] **Step 1: Write failing migration tests**

Create `tests/test_ai_script_migration.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import embed_eval_generate  # noqa: E402
import run_trigger_eval  # noqa: E402
import vault_merge  # noqa: E402


def test_vault_merge_uses_large_tier_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "# Merged Note\n\nMerged content."

    monkeypatch.setattr(vault_merge.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result = vault_merge.call_claude("merge prompt", model=None, timeout=12)

    assert result == "# Merged Note\n\nMerged content."
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "large"
    assert calls[0]["timeout"] == 12
    assert calls[0]["purpose"] == "vault-merge"


def test_run_trigger_eval_uses_small_tier_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "YES"

    monkeypatch.setattr(run_trigger_eval.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    assert run_trigger_eval.evaluate_trigger("query", "skill", "description") is True
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "trigger-eval"


def test_embed_eval_generate_uses_small_tier_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return '["query one", "query two"]'

    monkeypatch.setattr(embed_eval_generate.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result = embed_eval_generate.generate_queries_for_note("Note", "Body text")

    assert result == ["query one", "query two"]
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "embed-eval-generate"
```

Adjust function names only if actual exported names differ; inspect files before editing tests.

- [ ] **Step 2: Add failing vault_doctor AI helper test**

In `tests/test_vault_doctor.py`, add a targeted test for `call_claude_repair` and/or the prefix filter helper, matching actual function names. For `call_claude_repair`, use:

```python
def test_call_claude_repair_uses_small_tier_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "---\ntitle: Fixed\ntags: [test]\n---\n# Fixed\n"

    monkeypatch.setattr(vault_doctor.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result = vault_doctor.call_claude_repair("repair prompt", model=None, timeout=10)

    assert result is not None
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["timeout"] == 10
    assert calls[0]["purpose"] == "vault-doctor"
```

If the function is named differently, search `vault_doctor.py` for the two direct `claude -p` blocks and test those function names.

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_ai_script_migration.py tests/test_vault_doctor.py::test_call_claude_repair_uses_small_tier_backend -q
```

Expected: fail because modules do not import `ai_backend` or still call `subprocess.run` directly.

- [ ] **Step 4: Migrate `vault_merge.py`**

Add:

```python
import ai_backend
```

Replace `call_claude()` direct subprocess with:

```python
def call_claude(prompt: str, model: str | None, timeout: int) -> str | None:
    """Run merge synthesis through the configured prompt AI backend."""
    return ai_backend.run_ai_prompt(
        prompt,
        model=model,
        model_tier="large",
        timeout=timeout,
        purpose="vault-merge",
    )
```

Ensure the CLI default model is not a Claude model when omitted. If argparse currently uses `_DEFAULT_MODEL`, change `--model default=None` and resolve help text to mention backend defaults.

- [ ] **Step 5: Migrate `vault_doctor.py`**

Add:

```python
import ai_backend
```

For each direct `claude -p` helper, replace subprocess logic with:

```python
    return ai_backend.run_ai_prompt(
        prompt,
        model=model,
        model_tier="small",
        timeout=timeout,
        purpose="vault-doctor",
    )
```

Preserve existing timeout state transitions. If code distinguishes timeout from generic failure by catching `subprocess.TimeoutExpired`, update it to treat `None` as failure using the existing failed/timeout retry behavior only if no separate timeout signal is required. Do not add new public states.

- [ ] **Step 6: Migrate `run_trigger_eval.py`**

Add:

```python
import ai_backend
```

Replace direct subprocess in `evaluate_trigger()` with:

```python
    response = ai_backend.run_ai_prompt(
        prompt,
        model=None,
        model_tier="small",
        timeout=30,
        purpose="trigger-eval",
    )
    if not response:
        return False
    response = response.strip().upper()
```

If the script has a `MODEL` constant, leave it for Claude historical docs only if still used; otherwise remove it.

- [ ] **Step 7: Migrate `embed_eval_generate.py`**

Add:

```python
import ai_backend
```

Replace `_call_claude()` body with:

```python
def _call_claude(prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str | None:
    """Run an eval-generation prompt through the configured AI backend."""
    return ai_backend.run_ai_prompt(
        prompt,
        model=None,
        model_tier="small",
        timeout=timeout,
        purpose="embed-eval-generate",
    )
```

Optionally rename to `_call_ai`; if renaming, update all internal references and tests. Do not change CLI behavior beyond backend support.

- [ ] **Step 8: Run focused tests to verify GREEN**

Run:

```bash
uv run pytest tests/test_ai_script_migration.py tests/test_vault_doctor.py tests/test_embed_eval.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Search for remaining direct `claude -p` prompt subprocesses**

Run:

```bash
rg '"claude"|claude -p|_call_claude' skills/parsidion/scripts -g '*.py'
```

Expected remaining matches are allowed only in:

- `summarize_sessions.py` comments/docs/imports because SDK work is out of scope
- documentation strings describing legacy behavior that have been updated to mention backend if user-facing
- non-prompt references that are not subprocess AI calls

If a direct prompt-style `claude -p` subprocess remains outside `summarize_sessions.py`, migrate it or document why it is out of scope in the commit message.

- [ ] **Step 10: Format, lint, and commit Task 3**

Run:

```bash
uv run ruff format skills/parsidion/scripts/vault_doctor.py skills/parsidion/scripts/vault_merge.py skills/parsidion/scripts/run_trigger_eval.py skills/parsidion/scripts/embed_eval_generate.py tests/test_ai_script_migration.py tests/test_vault_doctor.py
uv run ruff check skills/parsidion/scripts/vault_doctor.py skills/parsidion/scripts/vault_merge.py skills/parsidion/scripts/run_trigger_eval.py skills/parsidion/scripts/embed_eval_generate.py tests/test_ai_script_migration.py tests/test_vault_doctor.py
uv run pyright skills/parsidion/scripts/vault_doctor.py skills/parsidion/scripts/vault_merge.py skills/parsidion/scripts/run_trigger_eval.py skills/parsidion/scripts/embed_eval_generate.py
```

Commit:

```bash
git add skills/parsidion/scripts/vault_doctor.py skills/parsidion/scripts/vault_merge.py skills/parsidion/scripts/run_trigger_eval.py skills/parsidion/scripts/embed_eval_generate.py tests/test_ai_script_migration.py tests/test_vault_doctor.py tests/test_embed_eval.py
git commit -m "feat: migrate prompt ai scripts to backend helper"
```

---

### Task 4: Documentation and config examples

**Files:**
- Modify: `README.md`
- Modify: `skills/parsidion/SKILL.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update README config section**

In `README.md`, update the configuration block near the existing `anthropic_env` and model defaults with:

```yaml
ai:
  backend: auto          # auto | claude-cli | codex-cli | none

ai_models:
  claude:
    small: claude-haiku-4-5-20251001
    large: claude-sonnet-4-6
  codex:
    small: gpt-5.5
    large: gpt-5.5

codex_cli:
  command: codex
  timeout: 60
  sandbox: read-only
  ephemeral: true
  skip_git_repo_check: true
```

Add text below the block:

```markdown
`ai.backend` controls prompt-style AI helpers used by session-start selection, session-stop classification, vault doctor repairs, vault merge synthesis, and eval utilities. `auto` prefers the active runtime when Parsidion can detect it: Codex runtime hints use `codex exec`, Claude runtime hints use `claude -p`, and ambiguous environments keep the historical Claude CLI behavior.

Codex mode uses the Codex CLI and its normal authentication path. Parsidion does not read, copy, or manage `~/.codex/auth.json`, and this is not OpenAI API-key provider support. Prompt-style Codex calls default to `codex exec --ephemeral --sandbox read-only --skip-git-repo-check` and write/read the final answer via `--output-last-message`.

`summarize_sessions.py` still uses `claude-agent-sdk` in this release; Codex support for SDK-backed summarization is planned as a separate follow-up.
```

- [ ] **Step 2: Update script table references**

In `README.md`, update rows that say `claude -p` only:

- `session_start_hook.py`: mention `--ai` uses configured prompt AI backend.
- `vault_merge.py`: mention backend-aware AI-assisted merging.
- `vault_doctor.py`: mention backend-aware prompt AI repairs for repairable issues.
- Troubleshooting `CLAUDECODE` text: keep Claude-specific note, add that Codex backend uses `codex exec` and internal recursion guard.

- [ ] **Step 3: Update skill configuration docs**

In `skills/parsidion/SKILL.md`, add the same `ai`, `ai_models`, and `codex_cli` config excerpt. Keep it shorter than README but include these two notes:

```markdown
- `auto` prefers the active runtime when detectable and falls back to Claude CLI when ambiguous.
- Codex backend uses `codex exec`; Parsidion does not manage Codex auth files. `summarize_sessions.py` remains Claude Agent SDK-backed for now.
```

- [ ] **Step 4: Update changelog**

At the top unreleased section of `CHANGELOG.md`, add:

```markdown
- **Codex CLI AI backend for prompt-style scripts** — Parsidion can now route `claude -p`-style helper calls through `codex exec` with `ai.backend: codex-cli` or runtime-aware `auto` detection. Backend-specific model defaults prevent Claude model IDs from being passed to Codex; Codex large/synthesis tasks default to `gpt-5.5`.
```

If no `Unreleased` section exists, add under the current top version’s `Added` section using the project’s existing changelog style.

- [ ] **Step 5: Verify docs mention SDK limitation**

Run:

```bash
rg 'summarize_sessions.py.*claude-agent-sdk|claude-agent-sdk.*summarize_sessions.py|Codex.*auth|ai.backend' README.md skills/parsidion/SKILL.md CHANGELOG.md
```

Expected: output shows `ai.backend`, Codex auth clarification, and summarizer SDK limitation.

- [ ] **Step 6: Commit Task 4**

```bash
git add README.md skills/parsidion/SKILL.md CHANGELOG.md
git commit -m "docs: document codex cli ai backend"
```

---

### Task 5: Integration verification and final review fixes

**Files:**
- Modify only files needed to fix verification or review findings.

- [ ] **Step 1: Run targeted verification**

Run:

```bash
uv run pytest tests/test_ai_backend.py tests/test_session_start_hook.py tests/test_session_stop_hook.py tests/test_ai_script_migration.py tests/test_vault_doctor.py tests/test_hook_integration.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run direct Codex command construction smoke test through unit tests**

Run:

```bash
uv run pytest tests/test_ai_backend.py::TestRunAiPrompt::test_codex_command_reads_output_last_message -q
```

Expected: test passes without invoking real `codex` because subprocess is monkeypatched.

- [ ] **Step 3: Search for accidental SDK/summarizer edits**

Run:

```bash
git diff main...HEAD -- skills/parsidion/scripts/summarize_sessions.py
```

Expected: no diff. If there is a diff, revert it unless a prior task explicitly documented a necessary comment-only change.

- [ ] **Step 4: Search for Claude model leakage in Codex tests**

Run:

```bash
rg 'codex.*claude-|gpt-5.5|ai_models' tests skills/parsidion/scripts README.md skills/parsidion/SKILL.md
```

Expected: Codex defaults in tests/docs show `gpt-5.5`; no test expects Codex command `--model claude-*`.

- [ ] **Step 5: Run full verification**

Run:

```bash
make checkall
```

Expected:

- `ruff format` completes without unwanted unrelated rewrites.
- `ruff check` passes.
- `pyright` reports 0 errors.
- `pytest tests/` passes.

- [ ] **Step 6: Commit verification fixes if needed**

If Step 5 modified files or fixes were needed:

```bash
git status --short
git add <changed-files>
git commit -m "fix: finalize codex cli ai backend"
```

If no changes were needed, do not create an empty commit.

- [ ] **Step 7: Final status**

Run:

```bash
git status --short --branch
git log --oneline --max-count=8
```

Expected: clean worktree on `feature/codex-ai-backend`, with spec, helper, migration, docs, and any final fix commits visible.
