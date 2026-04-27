#!/usr/bin/env python3
"""Backend-neutral prompt AI helpers for Parsidion scripts."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

import vault_common
import vault_config

AiBackend = Literal["claude-cli", "codex-cli", "none"]
ModelTier = Literal["small", "large"]

_CONFIG_BACKEND_AUTO = "auto"
_DEFAULT_CLAUDE_TIMEOUT: int = 30
_DEFAULT_CODEX_TIMEOUT: int = 60
_DEFAULT_CLAUDE_MODELS: dict[ModelTier, str] = {
    "small": "claude-haiku-4-5-20251001",
    "large": "claude-sonnet-4-6",
}
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
            timeout=timeout if timeout is not None else _DEFAULT_CLAUDE_TIMEOUT,
            cwd=str(cwd) if cwd is not None else None,
            env=vault_common.env_without_claudecode(vault=vault),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None
    output = result.stdout.strip()
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


def _run_codex_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
) -> str | None:
    command = _config_str("codex_cli", "command", "codex", vault=vault)
    codex_timeout = (
        timeout
        if timeout is not None
        else _config_timeout(
            "codex_cli", "timeout", _DEFAULT_CODEX_TIMEOUT, vault=vault
        )
    )
    sandbox = _config_optional_str("codex_cli", "sandbox", "read-only", vault=vault)
    ephemeral = _config_bool("codex_cli", "ephemeral", True, vault=vault)
    skip_git_repo_check = _config_bool(
        "codex_cli", "skip_git_repo_check", True, vault=vault
    )

    output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="parsidion-codex-",
            delete=False,
        ) as output_file:
            output_path = Path(output_file.name)

        cmd = [command, "exec"]
        if ephemeral:
            cmd.append("--ephemeral")
        if sandbox is not None:
            cmd.extend(["--sandbox", sandbox])
        if skip_git_repo_check:
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
            env=_codex_env(),
        )
        if result.returncode != 0:
            return None
        output = output_path.read_text(encoding="utf-8").strip()
        return output or None
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
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
        )
    return _run_claude_prompt(
        prompt,
        model=resolved_model,
        timeout=timeout,
        cwd=cwd,
        vault=vault,
    )
