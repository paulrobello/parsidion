#!/usr/bin/env python3
"""Backend-neutral prompt AI helpers for Parsidion scripts.

ARC-006: lives in the ``core/`` package (stdlib-only, under the
``tests/test_stdlib_only.py`` gate); the flat ``ai_backend.py`` name at the
scripts root remains as a re-export shim for existing importers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal, cast

from . import subproc_util, vault_config, vault_fs, vault_path
from .vault_hooks import env_without_claudecode

__all__: list[str] = [
    "AiBackend",
    "AiBackendTimeout",
    "CAUSE_DISABLED",
    "CAUSE_EMPTY",
    "CAUSE_LAUNCH",
    "CAUSE_NONZERO",
    "CAUSE_TIMEOUT",
    "ModelTier",
    "resolve_ai_backend",
    "resolve_ai_model",
    "run_ai_prompt",
    "run_ai_prompt_with_cause",
]

AiBackend = Literal["claude-cli", "codex-cli", "grok-cli", "none"]
ModelTier = Literal["small", "large"]

_CONFIG_BACKEND_AUTO = "auto"
_DEFAULT_CLAUDE_TIMEOUT: int = 30
_DEFAULT_CODEX_TIMEOUT: int = 60
# grok-4.6 headless (`grok --prompt-file`) measured 17-40s per parsidion-sized
# prompt (cold OAuth/session start on the high end), so the default budget is
# generous; summarization prompts are larger than selector prompts.
_DEFAULT_GROK_TIMEOUT: int = 120
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
# Both tiers map to grok-4.6 today (the tier split is a no-op, mirroring the
# Codex default); override per tier via ``ai_models.grok.{small,large}``.
_DEFAULT_GROK_MODELS: dict[ModelTier, str] = {
    "small": "grok-4.6",
    "large": "grok-4.6",
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
# Grok needs the same minimal env as Codex (OAuth creds live in ~/.grok, so
# HOME is the only required secret-adjacent variable).
_GROK_ENV_KEYS: frozenset[str] = frozenset(
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
    prefers strong Codex/Grok runtime hints before the Claude fallback.
    """
    configured = _configured_backend(vault=vault)
    if configured in {"claude-cli", "codex-cli", "grok-cli", "none"}:
        return cast(AiBackend, configured)
    if configured != _CONFIG_BACKEND_AUTO:
        return "claude-cli"

    runtime_hint = os.environ.get("PARSIDION_RUNTIME", "").strip().lower()
    if runtime_hint == "codex":
        return "codex-cli"
    if runtime_hint == "grok":
        return "grok-cli"
    if runtime_hint == "claude":
        return "claude-cli"

    if os.environ.get("CODEX_SANDBOX") or os.environ.get("CODEX_SESSION_ID"):
        return "codex-cli"
    if os.environ.get("CLAUDECODE"):
        return "claude-cli"
    return "claude-cli"


def _model_from_config(
    backend_key: Literal["claude", "codex", "grok"],
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
    if backend == "grok-cli":
        return _model_from_config("grok", model_tier, _DEFAULT_GROK_MODELS, vault)
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
        # SEC-024: clamp caller-sourced timeouts too — inf previously meant
        # "wait forever" and nan raised at subprocess call time. Invalid
        # values fall back to 0, this helper's no-timeout sentinel.
        timeout=float(vault_config.clamp_timeout(timeout, 0, lo=1, hi=86400)),
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


# Cause labels returned by ``run_ai_prompt_with_cause`` alongside the text
# (``None`` on success). Lets the summarizer record *why* a prompt yielded
# nothing instead of collapsing four distinct backend outcomes into one None,
# so timeout / launch-failure / non-zero-exit / empty-output are distinguishable
# in logs and the dead-letter queue.
CAUSE_TIMEOUT = "timeout"
CAUSE_LAUNCH = "launch"
CAUSE_NONZERO = "nonzero"
CAUSE_EMPTY = "empty"
CAUSE_DISABLED = "disabled"


def _run_claude_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
    raise_on_timeout: bool,
) -> tuple[str | None, str | None]:
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

    # claude_cli.minimal_context (default true): replace the system prompt and
    # run from a clean scratch cwd so the project's CLAUDE.md chain is not
    # ingested — parsidion prompts are self-contained text transforms.
    minimal_context = _config_bool("claude_cli", "minimal_context", True, vault=vault)
    run_cwd: str | None = str(cwd) if cwd is not None else None
    if minimal_context:
        system_prompt = _config_str(
            "claude_cli", "system_prompt", _MINIMAL_SYSTEM_PROMPT, vault=vault
        )
        cmd.extend(["--system-prompt", system_prompt])
        run_cwd = str(_minimal_context_cwd())

    try:
        result = _run_prompt_subprocess(
            cmd,
            timeout=timeout
            if timeout is not None
            else _config_timeout(
                "claude_cli", "timeout", _DEFAULT_CLAUDE_TIMEOUT, vault=vault
            ),
            cwd=run_cwd,
            env=env_without_claudecode(vault=vault),
            stdin=prompt,
        )
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise AiBackendTimeout("AI backend prompt timed out") from exc
        return None, CAUSE_TIMEOUT
    except OSError:
        return None, CAUSE_LAUNCH

    if result is None:
        # Launch failure (binary not found, etc.) — helper already
        # swallowed the OSError; no extra diagnostics to log here.
        return None, CAUSE_LAUNCH

    if result.returncode != 0:
        _log_backend_failure(
            "claude -p", result.returncode, result.stdout, result.stderr
        )
        return None, CAUSE_NONZERO
    output = _extract_claude_json_result(result.stdout)
    if not output:
        _log_backend_failure(
            "claude -p", result.returncode, result.stdout, result.stderr
        )
        return None, CAUSE_EMPTY
    return output, None


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
    # SEC-024: nan / negative / inf config values must not reach
    # subprocess.run(timeout=...); clamp into [1, 3600] with the default
    # for anything non-finite or non-positive.
    return vault_config.clamp_timeout(value, default)


def _resolve_configured_binary(
    command: str, label: str, default_command: str | None = None
) -> str:
    """Resolve a configured CLI ``command`` value to an executable path.

    SEC-117: config-file values that become ``subprocess.argv[0]`` get the
    same gate ``par_mem.binary`` already has. Bare names resolve via
    ``shutil.which``; values with a path separator must point at an existing
    executable file. Raises ``FileNotFoundError`` when the binary cannot be
    resolved so the caller can fall back rather than silently launching a
    wrong binary.

    SEC-007: a path-like value must additionally pass
    ``vault_fs.is_trusted_executable`` (owned by the current uid, not
    group/world-writable) — a synced config.yaml must not be able to point
    the backend at an attacker-writable script. On failure the *default*
    command name (when given) is resolved via ``shutil.which`` as a
    fallback; if that also fails, ``FileNotFoundError`` propagates.
    """
    if not command:
        raise FileNotFoundError(f"{label} is empty")
    # Bare command name: resolve via PATH.
    if "/" not in command and not os.path.isabs(command):
        resolved = shutil.which(command)
        if not resolved:
            raise FileNotFoundError(
                f"{label} {command!r} not found on PATH; "
                f"install the CLI or set {label} to an absolute path."
            )
        return resolved
    # Path-like value: must point at an existing executable.
    candidate = Path(command)
    if not candidate.exists():
        raise FileNotFoundError(
            f"{label} {command!r} does not exist; "
            f"set {label} to an absolute path to the executable."
        )
    if not os.access(candidate, os.X_OK):
        raise FileNotFoundError(f"{label} {command!r} is not executable.")
    if not vault_fs.is_trusted_executable(candidate):
        if default_command is not None:
            fallback = shutil.which(default_command)
            if fallback:
                sys.stderr.write(
                    f"[ai_backend] {label} {command!r} failed the trust "
                    f"check (not owned by the current user or group/world-"
                    f"writable); using {fallback!r} from PATH instead. "
                    f"SEC-007\n"
                )
                sys.stderr.flush()
                return fallback
        raise FileNotFoundError(
            f"{label} {command!r} failed the trust check "
            f"(not owned by the current user or group/world-writable)."
        )
    return str(candidate.resolve())


def _resolve_codex_command(command: str) -> str:
    """Resolve ``codex_cli.command`` (SEC-117/SEC-007 gates; shared helper)."""
    return _resolve_configured_binary(command, "codex_cli.command", "codex")


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
) -> tuple[str | None, str | None]:
    command_raw = _config_str("codex_cli", "command", "codex", vault=vault)
    try:
        command = _resolve_codex_command(command_raw)
    except FileNotFoundError as exc:
        # ARC-048e: log the failure so the user can diagnose, then fall
        # back to None (the caller's existing fallback path).
        sys.stderr.write(f"[ai_backend] {exc}\n")
        sys.stderr.flush()
        return None, CAUSE_LAUNCH

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
            return None, CAUSE_LAUNCH
        if result.returncode != 0:
            # ARC-048e: log the failure so empty-result failures are
            # diagnosable on the Codex path (parity with _run_claude_prompt).
            _log_backend_failure(
                "codex exec", result.returncode, result.stdout, result.stderr
            )
            return None, CAUSE_NONZERO
        try:
            output = output_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            # ARC-048e: this failure mode used to be silent; log it.
            sys.stderr.write(
                f"[ai_backend] codex exec rc=0 but output file {output_path} "
                "could not be read.\n"
            )
            sys.stderr.flush()
            return None, CAUSE_EMPTY
        if not output:
            # ARC-048e: empty output despite rc=0 — log so the user can
            # tell this apart from a launch failure.
            sys.stderr.write(
                "[ai_backend] codex exec rc=0 but produced no output "
                f"(stderr={result.stderr.strip()[:200]!r}).\n"
            )
            sys.stderr.flush()
            return None, CAUSE_EMPTY
        return output, None
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise AiBackendTimeout("AI backend prompt timed out") from exc
        return None, CAUSE_TIMEOUT
    except OSError:
        return None, CAUSE_LAUNCH
    finally:
        if output_path is not None:
            try:
                output_path.unlink(missing_ok=True)
            except OSError:
                pass


_MINIMAL_SYSTEM_PROMPT = (
    "You are a text transformation assistant for a note-taking system. "
    "Follow the user's instructions exactly and output only the requested "
    "result with no extra commentary."
)


def _grok_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _GROK_ENV_KEYS}
    env["PARSIDION_INTERNAL"] = "1"
    return env


def _minimal_context_cwd() -> Path:
    """Return an empty non-git scratch dir for minimal-context CLI calls.

    Grok scans repo-root → cwd for ``Agents.md`` / ``Claude.md`` (and loads
    its skill catalog from the project), appending everything to the system
    prompt. Running from an empty scratch dir outside any git repo keeps
    those prompts hermetic.

    SEC-004: the scratch dir used to be a predictable shared-tmp path
    (``/tmp/parsidion-grok-clean``, ``mkdir(exist_ok=True)``, no ownership or
    mode check). ``claude -p`` loads ``<cwd>/.claude/settings.json``, so a
    co-tenant on a multi-user host could pre-create the directory (or plant
    settings inside it). The dir now lives under ``secure_log_dir()`` (0700),
    is verified to be a non-symlink owned by the current uid with no
    group/other bits, and falls back to a private ``mkdtemp`` when any check
    fails. It stays empty: no ``.claude/``, no ``CLAUDE.md``.
    """
    clean = vault_path.secure_log_dir() / "clean-cwd"
    try:
        clean.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = clean.lstat()
        if (
            clean.is_symlink()
            or st.st_uid != os.getuid()
            or st.st_mode & 0o077  # group/other permission bits
        ):
            raise PermissionError(f"untrusted scratch dir: {clean}")
        os.chmod(clean, 0o700)
        return clean
    except OSError:
        pass
    return Path(tempfile.mkdtemp(prefix="parsidion-clean-"))


def _run_grok_prompt(
    prompt: str,
    *,
    model: str | None,
    timeout: int | float | None,
    cwd: str | Path | None,
    vault: Path | None,
    raise_on_timeout: bool,
) -> tuple[str | None, str | None]:
    """Run a single-turn grok prompt via ``--prompt-file``.

    ``grok_cli.minimal_context`` (default true) overrides the system prompt
    and runs from a clean scratch cwd with tools, subagents, and web search
    disabled — without it grok ingests the project's CLAUDE.md/AGENTS.md
    rules and its full skill catalog, which is dead context for parsidion's
    selector/summarizer prompts. SEC-123: the prompt travels via a temp
    file, not ``argv``.
    """
    command_raw = _config_str("grok_cli", "command", "grok", vault=vault)
    try:
        command = _resolve_configured_binary(command_raw, "grok_cli.command", "grok")
    except FileNotFoundError as exc:
        sys.stderr.write(f"[ai_backend] {exc}\n")
        sys.stderr.flush()
        return None, CAUSE_LAUNCH

    grok_timeout = (
        timeout
        if timeout is not None
        else _config_timeout("grok_cli", "timeout", _DEFAULT_GROK_TIMEOUT, vault=vault)
    )
    minimal_context = _config_bool("grok_cli", "minimal_context", True, vault=vault)

    prompt_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix="parsidion-grok-",
            delete=False,
        ) as prompt_file:
            prompt_file.write(prompt)
            prompt_path = Path(prompt_file.name)

        cmd = [command, "--prompt-file", str(prompt_path), "--verbatim"]
        if model:
            cmd.extend(["--model", model])
        if minimal_context:
            system_prompt = _config_str(
                "grok_cli", "system_prompt", _MINIMAL_SYSTEM_PROMPT, vault=vault
            )
            cmd.extend(
                [
                    "--cwd",
                    str(_minimal_context_cwd()),
                    "--system-prompt-override",
                    system_prompt,
                    "--tools",
                    "",
                    "--no-subagents",
                    "--disable-web-search",
                ]
            )

        result = _run_prompt_subprocess(
            cmd,
            timeout=grok_timeout,
            cwd=str(cwd) if cwd is not None else None,
            env=_grok_env(),
        )
        if result is None:
            return None, CAUSE_LAUNCH
        if result.returncode != 0:
            _log_backend_failure(
                "grok -p", result.returncode, result.stdout, result.stderr
            )
            return None, CAUSE_NONZERO
        output = (result.stdout or "").strip()
        if not output:
            _log_backend_failure(
                "grok -p", result.returncode, result.stdout, result.stderr
            )
            return None, CAUSE_EMPTY
        return output, None
    except subprocess.TimeoutExpired as exc:
        if raise_on_timeout:
            raise AiBackendTimeout("AI backend prompt timed out") from exc
        return None, CAUSE_TIMEOUT
    except OSError:
        return None, CAUSE_LAUNCH
    finally:
        if prompt_path is not None:
            try:
                prompt_path.unlink(missing_ok=True)
            except OSError:
                pass


def run_ai_prompt_with_cause(
    prompt: str,
    *,
    model: str | None = None,
    model_tier: ModelTier = "small",
    timeout: int | float | None = None,
    cwd: str | Path | None = None,
    purpose: str = "general",
    vault: Path | None = None,
    raise_on_timeout: bool = False,
) -> tuple[str | None, str | None]:
    """Run a prompt through the configured prompt AI backend, returning a cause.

    Returns ``(text, cause)`` where ``text`` is the assistant's reply (or
    ``None``) and ``cause`` is ``None`` on success, else one of the ``CAUSE_*``
    labels (timeout/launch/nonzero/empty/disabled) naming *why* no text was
    produced. Use this when the caller records the failure reason (e.g. the
    summarizer's dead-letter classification); prefer :func:`run_ai_prompt` when
    only the text matters.
    """
    del purpose
    backend = resolve_ai_backend(vault=vault)
    if backend == "none":
        return None, CAUSE_DISABLED

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
    if backend == "grok-cli":
        return _run_grok_prompt(
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
    callers can preserve their existing heuristic/fallback paths. Thin wrapper
    over :func:`run_ai_prompt_with_cause`; callers that need to distinguish
    *why* the backend returned nothing should call that directly.
    """
    return run_ai_prompt_with_cause(
        prompt,
        model=model,
        model_tier=model_tier,
        timeout=timeout,
        cwd=cwd,
        purpose=purpose,
        vault=vault,
        raise_on_timeout=raise_on_timeout,
    )[0]
