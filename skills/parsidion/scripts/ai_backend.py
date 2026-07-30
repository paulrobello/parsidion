#!/usr/bin/env python3
"""Backend-neutral prompt AI helpers for Parsidion scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

import subproc_util
import vault_config
from vault_hooks import env_without_claudecode

AiBackend = Literal["claude-cli", "codex-cli", "none"]
ModelTier = Literal["small", "large"]

_CONFIG_BACKEND_AUTO = "auto"
_DEFAULT_CLAUDE_TIMEOUT: int = 30
_DEFAULT_CODEX_TIMEOUT: int = 60
# ARC-006: These hardcoded model identifiers are deprecation risks — Anthropic
# periodically retires dated model snapshots (e.g. ``-20251001`` suffixes).
# Override them without touching this file by setting either:
#   • ``defaults.haiku_model`` in config.yaml, or
#   • the standard Anthropic env vars: ``ANTHROPIC_DEFAULT_HAIKU_MODEL``,
#     ``ANTHROPIC_DEFAULT_SONNET_MODEL`` (honoured by the claude CLI).
# The ``ai_models.claude`` config section (``small``/``large`` keys) also
# takes precedence over these defaults via ``_model_from_config()``.
# Note: ``defaults.sonnet_model`` is no longer read -- use ``ai_models.<backend>.large``
# instead (DOC-010).
_DEFAULT_CLAUDE_MODELS: dict[ModelTier, str] = {
    "small": "claude-haiku-4-5-20251001",
    "large": "claude-sonnet-4-6",
}
# ARC-013: Both Codex tiers intentionally map to the same model identifier.
# The Codex CLI (``codex exec``) does not currently expose a public API for
# selecting a tier/size variant, so the ``small``/``large`` distinction is a
# no-op here.  When Codex adds tiered model selection, update this dict and
# add a config key under ``ai_models.codex`` in config.yaml.
_DEFAULT_CODEX_MODELS: dict[ModelTier, str] = {
    "small": "gpt-5.5",
    "large": "gpt-5.5",
}
_CODEX_ENV_KEYS: frozenset[str] = frozenset(
    {
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
)


class AiBackendTimeout(RuntimeError):
    """Raised when an AI backend prompt times out and timeout raising is enabled."""


def _load_config(vault: Path | None = None) -> dict[str, Any]:
    return vault_config.load_config(vault=vault)


def _section(name: str, vault: Path | None = None) -> dict[str, Any]:
    value = _load_config(vault=vault).get(name)
    return value if isinstance(value, dict) else {}


def _config_value(
    section: str, key: str, default: Any, vault: Path | None = None
) -> Any:
    section_dict = _section(section, vault=vault)
    return section_dict[key] if key in section_dict else default


def _configured_backend(vault: Path | None = None) -> str:
    value = _config_value("ai", "backend", _CONFIG_BACKEND_AUTO, vault=vault)
    if value is None:
        return _CONFIG_BACKEND_AUTO
    return str(value).strip().lower()


def resolve_ai_backend(vault: Path | None = None) -> AiBackend:
    """Resolve the configured prompt AI backend.

    Explicit ``ai.backend`` values win. ``auto`` inspects runtime hints and
    prefers strong Codex runtime hints before the Claude fallback.
    """
    configured = _configured_backend(vault=vault)
    if configured in {"claude-cli", "codex-cli", "none"}:
        return cast(AiBackend, configured)
    if configured != _CONFIG_BACKEND_AUTO:
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
    backend_key: Literal["claude", "codex"],
    model_tier: ModelTier,
    defaults: dict[ModelTier, str],
    vault: Path | None,
) -> str:
    backend_models = _config_value("ai_models", backend_key, None, vault=vault)
    if isinstance(backend_models, dict):
        configured = backend_models.get(model_tier)
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
    return defaults[model_tier]


def resolve_ai_model(
    backend: AiBackend,
    model: str | None = None,
    model_tier: ModelTier = "small",
    vault: Path | None = None,
) -> str | None:
    """Resolve an explicit model or the backend-specific tier default."""
    if model is not None and model.strip():
        return model.strip()
    if backend == "none":
        return None
    if backend == "codex-cli":
        return _model_from_config("codex", model_tier, _DEFAULT_CODEX_MODELS, vault)
    return _model_from_config("claude", model_tier, _DEFAULT_CLAUDE_MODELS, vault)


def _codex_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _CODEX_ENV_KEYS}
    env["PARSIDION_INTERNAL"] = "1"
    return env


def _run_prompt_subprocess(
    cmd: list[str],
    *,
    timeout: int | float,
    cwd: str | Path | None,
    env: dict[str, str],
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Run a prompt AI subprocess with process-group kill on timeout.

    SEC-122 / ARC-048f: thin wrapper over ``subproc_util.run_with_pgkill``,
    the single canonical implementation. Previously this module and
    ``parmem_backend._run_parmem`` each rolled their own SIGTERM → SIGKILL
    escalation and the two had already drifted. Returns ``None`` on launch
    failure (matches the prior ``OSError`` swallowing contract) and re-raises
    ``subprocess.TimeoutExpired`` semantics via the shared helper's
    ``"timeout"`` reason — translated back here so callers can keep their
    existing ``except subprocess.TimeoutExpired`` blocks.

    SEC-123: *stdin*, when given, is piped to the child instead of being
    passed as ``argv`` — keeps prompts up to ~12 KB out of ``ps auxww``.
    """
    reason, proc = subproc_util.run_with_pgkill(
        cmd,
        cwd=cwd,
        timeout=float(timeout) if timeout and timeout > 0 else 0,
        env=env,
        stdin=stdin,
    )
    if reason == "timeout":
        raise subprocess.TimeoutExpired(cmd, timeout)
    if reason == "launch" or proc is None:
        return None
    return proc


def _log_backend_failure(
    label: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    """Emit a diagnostic for a prompt-AI call that produced no usable output.

    Non-zero exits and CLI error messages were previously swallowed, making
    empty-result failures impossible to diagnose (the caller only saw ``None``).
    """
    snippet = (stderr or "").strip()
    if len(snippet) > 500:
        snippet = snippet[:500] + "…"
    parts = [
        f"[ai_backend] {label} yielded no usable result",
        f"rc={returncode}",
        f"stdout_len={len(stdout or '')}",
    ]
    if snippet:
        parts.append(f"stderr={snippet!r}")
    sys.stderr.write(" ".join(parts) + "\n")
    sys.stderr.flush()


def _extract_claude_json_result(stdout: str) -> str | None:
    """Extract the assistant text from a ``claude -p --output-format json`` run.

    ``--output-format json`` prints a single JSON envelope whose ``result``
    field holds the model's final text answer (populated even when the response
    includes thinking, which is not emitted to ``-p`` stdout). Returns ``None``
    when the envelope has no usable ``result``. Falls back to the raw stdout
    when it is not JSON, so older / non-JSON CLI output keeps working.
    """
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        envelope = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(envelope, dict):
        result_field = envelope.get("result")
        if isinstance(result_field, str):
            stripped = result_field.strip()
            if stripped:
                return stripped
        return None
    return text


def _run_claude_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
    raise_on_timeout: bool,
) -> str | None:
    # SEC-123: pass the prompt on stdin instead of as ``argv`` so up to
    # ~12 KB of transcript is not visible via ``ps auxww``. ``claude -p``
    # with no prompt positional reads from stdin.
    cmd = ["claude", "-p"]
    if model:
        cmd.extend(["--model", model])
    cmd.append("--no-session-persistence")
    # JSON output gives a reliable ``result`` field (the assistant's final text,
    # separate from any thinking) plus ``subtype``/``session_id`` for diagnostics,
    # instead of relying on plain-text stdout which can be empty for
    # thinking-dominated responses.
    cmd.extend(["--output-format", "json"])

    try:
        result = _run_prompt_subprocess(
            cmd,
            timeout=timeout if timeout is not None else _DEFAULT_CLAUDE_TIMEOUT,
            cwd=str(cwd) if cwd is not None else None,
            env=env_without_claudecode(vault=vault),
            stdin=prompt,
        )
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise AiBackendTimeout("AI backend prompt timed out") from exc
        return None
    except OSError:
        return None

    if result is None:
        # Launch failure (binary not found, etc.) — helper already
        # swallowed the OSError; no extra diagnostics to log here.
        return None

    if result.returncode != 0:
        _log_backend_failure(
            "claude -p", result.returncode, result.stdout, result.stderr
        )
        return None
    output = _extract_claude_json_result(result.stdout)
    if not output:
        _log_backend_failure(
            "claude -p", result.returncode, result.stdout, result.stderr
        )
    return output or None


def _config_str(section: str, key: str, default: str, vault: Path | None = None) -> str:
    value = _config_value(section, key, default, vault=vault)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _config_optional_str(
    section: str, key: str, default: str | None, vault: Path | None = None
) -> str | None:
    value = _config_value(section, key, default, vault=vault)
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return default


def _config_bool(
    section: str, key: str, default: bool, vault: Path | None = None
) -> bool:
    value = _config_value(section, key, default, vault=vault)
    return value if isinstance(value, bool) else default


def _config_timeout(
    section: str, key: str, default: int | float, vault: Path | None = None
) -> int | float:
    value = _config_value(section, key, default, vault=vault)
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    return default


def _resolve_codex_command(command: str) -> str:
    """Resolve a configured ``codex_cli.command`` value to an executable path.

    SEC-117: ``codex_cli.command`` is a config-file value that becomes
    ``subprocess.argv[0]``, so apply the same gate ``par_mem.binary``
    already gets. Bare names resolve via ``shutil.which``; values with a
    path separator must point at an existing executable file. Returns the
    resolved path on success, or the input unchanged when it already is
    an absolute executable path. Raises ``FileNotFoundError`` when the
    binary cannot be resolved so the caller can fall back rather than
    silently launching a wrong binary.
    """
    if not command:
        raise FileNotFoundError("codex_cli.command is empty")
    # Bare command name: resolve via PATH.
    if "/" not in command and not os.path.isabs(command):
        resolved = shutil.which(command)
        if not resolved:
            raise FileNotFoundError(
                f"codex_cli.command {command!r} not found on PATH; "
                "install codex or set codex_cli.command to an absolute path."
            )
        return resolved
    # Path-like value: must point at an existing executable.
    candidate = Path(command)
    if not candidate.exists():
        raise FileNotFoundError(
            f"codex_cli.command {command!r} does not exist; "
            "set codex_cli.command to an absolute path to a codex executable."
        )
    if not os.access(candidate, os.X_OK):
        raise FileNotFoundError(f"codex_cli.command {command!r} is not executable.")
    return str(candidate.resolve())


def _resolve_codex_sandbox(sandbox: str | None, vault: Path | None) -> str | None:
    """Validate ``codex_cli.sandbox`` against an allowlist.

    SEC-117: ``danger-full-access`` lets the model run arbitrary commands
    with no sandboxing. Reject it unless the user has set an explicit
    ``allow_danger_full_access`` opt-in. Other values pass through
    unchanged. ``None`` (explicit ``null`` in YAML) means "no sandbox
    flag at all" — preserved so the user can drop the flag entirely.
    """
    if sandbox is None:
        return None
    if sandbox == "danger-full-access":
        allow = _config_bool(
            "codex_cli", "allow_danger_full_access", False, vault=vault
        )
        if not allow:
            sys.stderr.write(
                "[ai_backend] codex_cli.sandbox='danger-full-access' refused "
                "(set codex_cli.allow_danger_full_access=true to override).\n"
            )
            sys.stderr.flush()
            return "read-only"
        sys.stderr.write(
            "[ai_backend] WARNING: codex_cli.sandbox='danger-full-access' is "
            "explicitly enabled — the model has full filesystem and command "
            "access. Do not enable on untrusted vault content.\n"
        )
        sys.stderr.flush()
    return sandbox


def _run_codex_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
    raise_on_timeout: bool,
) -> str | None:
    command_raw = _config_str("codex_cli", "command", "codex", vault=vault)
    try:
        command = _resolve_codex_command(command_raw)
    except FileNotFoundError as exc:
        # ARC-048e: log the failure so the user can diagnose, then fall
        # back to None (the caller's existing fallback path).
        sys.stderr.write(f"[ai_backend] {exc}\n")
        sys.stderr.flush()
        return None

    codex_timeout = (
        timeout
        if timeout is not None
        else _config_timeout(
            "codex_cli", "timeout", _DEFAULT_CODEX_TIMEOUT, vault=vault
        )
    )
    sandbox_raw = _config_optional_str("codex_cli", "sandbox", "read-only", vault=vault)
    sandbox = _resolve_codex_sandbox(sandbox_raw, vault=vault)
    ephemeral = _config_bool("codex_cli", "ephemeral", True, vault=vault)
    skip_git_repo_check = _config_bool(
        "codex_cli", "skip_git_repo_check", True, vault=vault
    )
    suppress_notify = _config_bool("codex_cli", "suppress_notify", True, vault=vault)

    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="parsidion-codex-",
            delete=False,
        ) as output_file:
            output_path = Path(output_file.name)

        # SEC-123: pass prompt on stdin (codex exec reads stdin when no
        # PROMPT positional is given, per `codex exec --help`).
        cmd = [command, "exec"]
        if suppress_notify:
            cmd.extend(["--config", "notify=[]"])
        if ephemeral:
            cmd.append("--ephemeral")
        if sandbox is not None:
            cmd.extend(["--sandbox", sandbox])
        if skip_git_repo_check:
            cmd.append("--skip-git-repo-check")
        cmd.extend(["--output-last-message", str(output_path)])
        if model:
            cmd.extend(["--model", model])

        result = _run_prompt_subprocess(
            cmd,
            timeout=codex_timeout,
            cwd=str(cwd) if cwd is not None else None,
            env=_codex_env(),
            stdin=prompt,
        )
        if result is None:
            # Launch failure — helper already swallowed OSError. No
            # stdout/stderr to log here.
            return None
        if result.returncode != 0:
            # ARC-048e: log the failure so empty-result failures are
            # diagnosable on the Codex path (parity with _run_claude_prompt).
            _log_backend_failure(
                "codex exec", result.returncode, result.stdout, result.stderr
            )
            return None
        try:
            output = output_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            # ARC-048e: this failure mode used to be silent; log it.
            sys.stderr.write(
                f"[ai_backend] codex exec rc=0 but output file {output_path} "
                "could not be read.\n"
            )
            sys.stderr.flush()
            return None
        if not output:
            # ARC-048e: empty output despite rc=0 — log so the user can
            # tell this apart from a launch failure.
            sys.stderr.write(
                "[ai_backend] codex exec rc=0 but produced no output "
                f"(stderr={result.stderr.strip()[:200]!r}).\n"
            )
            sys.stderr.flush()
        return output or None
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise AiBackendTimeout("AI backend prompt timed out") from exc
        return None
    except OSError:
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
    raise_on_timeout: bool = False,
) -> str | None:
    """Run a prompt through the configured prompt AI backend.

    Returns ``None`` for disabled backends and all recoverable CLI failures so
    callers can preserve their existing heuristic/fallback paths.
    """
    del purpose
    backend = resolve_ai_backend(vault=vault)
    if backend == "none":
        return None

    resolved_model = resolve_ai_model(
        backend,
        model=model,
        model_tier=model_tier,
        vault=vault,
    )
    if backend == "codex-cli":
        return _run_codex_prompt(
            prompt,
            model=resolved_model,
            timeout=timeout,
            cwd=cwd,
            vault=vault,
            raise_on_timeout=raise_on_timeout,
        )
    return _run_claude_prompt(
        prompt,
        model=resolved_model,
        timeout=timeout,
        cwd=cwd,
        vault=vault,
        raise_on_timeout=raise_on_timeout,
    )
