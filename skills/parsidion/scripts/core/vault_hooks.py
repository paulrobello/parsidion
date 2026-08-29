"""Hook utilities, event logging, environment management, and transcript analysis.

Provides hook event logging, safe environment construction for child processes,
transcript text extraction, project name detection, process checking, and
transcript category detection/parsing shared by session_stop and subagent_stop hooks.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .vault_config import config_key_sources, load_config
from .vault_constants import TRANSCRIPT_CATEGORY_LABELS
from .vault_fs import (
    _git_path_ignored,
    write_hook_event,
)  # ARC-023: re-export (impl moved to vault_fs)
from .vault_path import resolve_vault, rotate_log_file, secure_log_dir

__all__: list[str] = [
    # Environment helpers
    "apply_configured_env_defaults",
    "env_without_claudecode",
    "SAFE_ENV_KEYS",  # QA-012: public alias
    "_SAFE_ENV_KEYS",  # private name; re-exported by vault_common for back-comat
    # Hook event logging
    "write_hook_event",
    # Persistent hook-error logging (QA-003)
    "log_hook_error",
    # Transcript helpers
    "extract_text_from_content",
    "allowed_transcript_roots",
    "codex_home",
    "antigravity_home",
    "is_allowed_transcript_path",
    "is_codex_transcript_path",
    "is_antigravity_transcript_path",
    "is_pi_transcript_path",
    # Project detection
    "get_project_name",
    # Process utilities
    "is_process_running",
    # Transcript analysis (shared by session_stop and subagent_stop hooks)
    "TRANSCRIPT_CATEGORIES",
    "TRANSCRIPT_CATEGORY_LABELS",
    "parse_transcript_lines",
    "parse_codex_transcript_lines",
    "parse_antigravity_transcript_lines",
    "detect_categories",
]

# ARC-023: write_hook_event (and its _HOOK_EVENTS_* constants) moved to
# vault_fs — the filesystem/I-O layer — to break the vault_fs <-> vault_hooks
# cycle (vault_fs.append_to_pending needs to log a drop event, which used to
# pull in vault_hooks, which itself imports vault_fs). It is re-exported here
# via the ``from .vault_fs import ...`` line below, so vault_hooks.write_hook_event
# and every ``from .vault_hooks import write_hook_event`` caller keep working.


def log_hook_error(hook_name: str) -> None:
    """Append a timestamped traceback entry to the persistent hook error log.

    QA-003: the single implementation of what was copy-pasted as
    ``_log_hook_error`` into five hook scripts. Called only from a hook's
    outermost ``except Exception`` handler so that unexpected programming
    errors (regressions, NameErrors, etc.) are written to
    ``~/.claude/logs/parsidion-hook-errors.log`` rather than disappearing
    into stderr. Best-effort — never raises.

    Args:
        hook_name: Short identifier for the hook (e.g. ``"session_stop_hook"``).
    """
    error_log = secure_log_dir() / "parsidion-hook-errors.log"
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        tb = traceback.format_exc()
        entry = f"[{ts}] {hook_name}\n{tb}\n"
        rotate_log_file(error_log)
        with open(error_log, "a", encoding="utf-8") as fh:
            fh.write(entry)
    except Exception as exc:  # noqa: BLE001 — logging must never raise
        print(f"hook error log write failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------

# Variables safe to pass through to child processes (avoids leaking secrets).
# SEC-006: ANTHROPIC_* vars are intentionally included so that non-default API
# configurations (proxy, org key, Bedrock, Vertex, corporate proxy) are
# forwarded to child ``claude -p`` processes for AI features to work.
#
# Included Anthropic vars and their purpose:
#   ANTHROPIC_API_KEY           -- API key (non-default / org / proxy setups)
#   ANTHROPIC_AUTH_TOKEN        -- Bearer token alternative to API key
#   ANTHROPIC_BASE_URL          -- Custom endpoint (proxy, gateway, Bedrock)
#   ANTHROPIC_CUSTOM_HEADERS    -- Extra HTTP headers (corp auth, tracing)
#   ANTHROPIC_DEFAULT_HAIKU_MODEL   -- Pinned haiku model ID
#   ANTHROPIC_DEFAULT_SONNET_MODEL  -- Pinned sonnet model ID
#   ANTHROPIC_DEFAULT_OPUS_MODEL    -- Pinned opus model ID
#   API_TIMEOUT_MS              -- API call timeout in milliseconds
#   HTTPS_PROXY / HTTP_PROXY    -- Corporate / network proxy
_SAFE_ENV_KEYS: frozenset[str] = frozenset(
    {
        # Shell / locale
        "PATH",
        "HOME",
        "USER",
        "SHELL",
        "TERM",
        "LANG",
        "LC_ALL",
        "TMPDIR",
        # Anthropic API auth & routing
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        # Model pinning
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        # Timeout
        "API_TIMEOUT_MS",
        # Network proxy
        "HTTPS_PROXY",
        "HTTP_PROXY",
        # parsight code-memory daemon endpoint (read by the parsight CLI and by
        # parsight_backend's health probe; safe: a URL, never a secret)
        "PARSIGHT_MCP_URL",
    }
)

# Config-backed env values that may be set under ``anthropic_env`` in the vault
# config. These mirror real environment variable names so users can copy values
# directly from external env-based configs such as ``~/.claude/glm-settings.json``.
_CONFIGURABLE_ENV_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "API_TIMEOUT_MS",
        "HTTPS_PROXY",
        "HTTP_PROXY",
    }
)

# SEC-007: keys that redirect where requests (and auth headers) are sent.
# Unlike the benign keys above, a value planted in a git-synced config.yaml
# can silently point the AI backends at an attacker-controlled endpoint, so
# they are honored only from config.local.yaml or a non-tracked config.yaml.
_NETWORK_AFFECTING_ENV_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_CUSTOM_HEADERS",
        "HTTPS_PROXY",
        "HTTP_PROXY",
    }
)

# Warn-once latch for refused network-affecting keys (per process).
_untrusted_network_env_warned = False

# QA-012: public alias — export SAFE_ENV_KEYS (no leading underscore) so callers
# can reference it without accessing a private name.  _SAFE_ENV_KEYS remains
# available for backward compatibility (referenced in docs and existing callers).
SAFE_ENV_KEYS: frozenset[str] = _SAFE_ENV_KEYS


def _coerce_env_value(value: object) -> str | None:
    """Convert a config value into a process env string.

    Empty strings and explicit ``null`` values are treated as unset.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.strip() else None
    return str(value)


def _configured_env_defaults(vault: Path | None = None) -> dict[str, str]:
    """Return supported env defaults from ``vault/config.yaml``.

    Environment variables are represented in config under the ``anthropic_env``
    section using their real env var names as keys.

    SEC-007: network-affecting keys (see ``_NETWORK_AFFECTING_ENV_KEYS``) are
    honored only when their effective value comes from ``config.local.yaml``
    or when ``config.yaml`` is not git-tracked in the vault repo — a synced
    ``config.yaml`` must not be able to redirect where requests and auth
    headers are sent. Refused keys are reported once on stderr.
    """
    global _untrusted_network_env_warned

    config = load_config(vault=vault)
    section = config.get("anthropic_env")
    if not isinstance(section, dict):
        return {}

    has_network_keys = any(key in section for key in _NETWORK_AFFECTING_ENV_KEYS)
    sources: dict[tuple[str, str], str] = (
        config_key_sources(vault=vault) if has_network_keys else {}
    )
    # Lazily computed: True only when the vault is a git repo AND config.yaml
    # is not gitignored there (i.e. the file syncs to a remote).
    tracked_config_yaml: bool | None = None

    resolved: dict[str, str] = {}
    refused: list[str] = []
    for key in _CONFIGURABLE_ENV_KEYS:
        if key not in section:
            continue
        value = _coerce_env_value(section[key])
        if value is None:
            continue
        if (
            key in _NETWORK_AFFECTING_ENV_KEYS
            and sources.get(("anthropic_env", key)) != "config.local.yaml"
        ):
            if tracked_config_yaml is None:
                vault_dir = vault if vault is not None else resolve_vault()
                tracked_config_yaml = (vault_dir / ".git").exists() and (
                    not _git_path_ignored("config.yaml", vault_dir)
                )
            if tracked_config_yaml:
                refused.append(key)
                continue
        resolved[key] = value

    if refused and not _untrusted_network_env_warned:
        _untrusted_network_env_warned = True
        print(
            "vault_hooks: ignoring anthropic_env network keys "
            f"{', '.join(sorted(refused))} from tracked config.yaml "
            "(honored only from config.local.yaml or a gitignored "
            "config.yaml); SEC-007",
            file=sys.stderr,
        )
    return resolved


def apply_configured_env_defaults(vault: Path | None = None) -> None:
    """Populate missing process env vars from ``vault/config.yaml``.

    Existing environment variables always win over config values.
    Call this before SDK-based Claude usage that reads from ``os.environ``
    directly instead of an explicit ``env=`` subprocess mapping.
    """
    for key, value in _configured_env_defaults(vault=vault).items():
        os.environ.setdefault(key, value)


def env_without_claudecode(vault: Path | None = None) -> dict[str, str]:
    """Return a filtered copy of the current environment for child processes.

    Only includes variables listed in ``_SAFE_ENV_KEYS``, which avoids leaking
    secrets or triggering the Claude nesting guard (``CLAUDECODE``).
    Missing supported Anthropic-compatible variables are filled from the vault
    config's ``anthropic_env`` section when present.

    Always injects ``PARSIDION_INTERNAL=1`` so that hook scripts invoked by the
    resulting ``claude -p`` session can detect and skip internal sessions.

    Returns:
        A dict suitable for passing as ``env=`` to ``subprocess.run`` / ``Popen``.
    """
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    for key, value in _configured_env_defaults(vault=vault).items():
        env.setdefault(key, value)
    env["PARSIDION_INTERNAL"] = "1"
    return env


# ---------------------------------------------------------------------------
# Transcript helpers
# ---------------------------------------------------------------------------


def extract_text_from_content(content: object) -> str:
    """Extract plain text from a transcript message content field.

    Content can be a plain string or an array of content blocks (each with
    a ``type`` and ``text`` field for text blocks).

    Args:
        content: The message content -- typically a string or list of blocks.

    Returns:
        Concatenated text from all text blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def codex_home() -> Path:
    """Return the configured Codex home directory."""
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()


def antigravity_home() -> Path:
    """Return the Antigravity (ex-Gemini CLI) config root — ``~/.gemini``."""
    return (
        Path(
            os.environ.get("ANTIGRAVITY_HOME", "")
            or os.environ.get("GEMINI_HOME", "~/.gemini")
        )
        .expanduser()
        .resolve()
    )


def allowed_transcript_roots(cwd: str | None = None) -> list[Path]:
    """Return allowed root directories for transcript files.

    Supports Claude Code, pi, Codex, and Gemini transcript locations:

    - ``~/.claude/`` (Claude Code transcripts)
    - ``~/.pi/`` (pi global transcripts, e.g. ``~/.pi/agent/sessions``)
    - ``<cwd>/.pi/`` (project-local pi transcripts, e.g. ``.pi/agent-sessions``)
    - ``$CODEX_HOME/sessions`` or ``~/.codex/sessions`` (Codex transcripts)
    - ``$GEMINI_HOME`` or ``~/.gemini`` (user Gemini transcripts)
    - ``<cwd>/.gemini/`` (project-local Gemini transcripts)

    Args:
        cwd: Optional working directory for project-local ``.pi``/``.gemini`` roots.

    Returns:
        De-duplicated list of resolved root paths.
    """
    roots: list[Path] = [
        Path.home() / ".claude",
        Path.home() / ".pi",
        codex_home() / "sessions",
        antigravity_home(),
    ]

    if cwd:
        try:
            cwd_path = Path(cwd).resolve()
            roots.append(cwd_path / ".pi")
            roots.append(cwd_path / ".gemini")
        except OSError:
            pass

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            deduped.append(resolved)

    return deduped


def is_allowed_transcript_path(transcript_path: Path, cwd: str | None = None) -> bool:
    """Return True when *transcript_path* is inside an allowed transcript root.

    SEC-010: Also requires the ``.jsonl`` suffix so that non-transcript files
    (e.g. ``settings.json``, ``CLAUDE.md``) cannot be read even if they reside
    under an allowed root such as ``~/.claude/``.
    """
    # SEC-010: Reject anything that is not a .jsonl file to prevent hook JSON
    # from pointing at non-transcript files (settings, keys, etc.) under ~/.claude.
    if transcript_path.suffix != ".jsonl":
        return False

    try:
        resolved = transcript_path.resolve()
    except OSError:
        return False

    for root in allowed_transcript_roots(cwd=cwd):
        try:
            if resolved.is_relative_to(root):
                return True
        except ValueError:
            continue

    return False


def is_codex_transcript_path(transcript_path: Path) -> bool:
    """Return True when *transcript_path* belongs to the Codex sessions root."""
    try:
        resolved = transcript_path.expanduser().resolve()
        root = (codex_home() / "sessions").resolve()
        return resolved == root or resolved.is_relative_to(root)
    except OSError:
        return False


def is_pi_transcript_path(transcript_path: Path, cwd: str | None = None) -> bool:
    """Return True when *transcript_path* belongs to a pi transcript root."""
    try:
        resolved = transcript_path.resolve()
    except OSError:
        return False

    roots: list[Path] = [Path.home() / ".pi"]
    if cwd:
        try:
            roots.append(Path(cwd).resolve() / ".pi")
        except OSError:
            pass

    for root in roots:
        try:
            if resolved.is_relative_to(root.resolve()):
                return True
        except (ValueError, OSError):
            continue

    return False


def is_antigravity_transcript_path(
    transcript_path: Path,
    cwd: str | None = None,  # noqa: ARG001
) -> bool:
    """True when *transcript_path* is an Antigravity CLI conversation transcript.

    Antigravity (the Gemini CLI successor) writes conversation logs ONLY at
    ``<root>/antigravity-cli/brain/<conversationId>/.system_generated/logs/
    transcript.jsonl`` where ``<root>`` is ``$ANTIGRAVITY_HOME`` /
    ``$GEMINI_HOME`` / ``~/.gemini``. The path must match that exact shape —
    the ``~/.gemini`` tree also holds settings, MCP configs, and legacy
    Gemini IDE data that must never be treated as transcripts, and no
    project-local transcript location is documented.
    """
    try:
        resolved = transcript_path.expanduser().resolve()
    except OSError:
        return False

    try:
        rel = resolved.relative_to(antigravity_home().resolve())
    except (ValueError, OSError):
        return False

    parts = rel.parts
    return (
        len(parts) == 6
        and parts[0] == "antigravity-cli"
        and parts[1] == "brain"
        and bool(parts[2])
        and parts[3] == ".system_generated"
        and parts[4] == "logs"
        and parts[5] == "transcript.jsonl"
    )


# ---------------------------------------------------------------------------
# Project detection
# ---------------------------------------------------------------------------


def get_project_name(cwd: str | None = None) -> str:
    """Extract a project name from *cwd* or the current directory.

    Uses the basename of the directory. If the directory is inside a git
    repository, uses the repository root basename instead.
    """
    if cwd is None:
        cwd = os.getcwd()

    path = Path(cwd).resolve()

    # Walk up to find a .git directory
    check = path
    while check != check.parent:
        if (check / ".git").exists():
            return check.name
        check = check.parent

    # Fallback: basename of the given directory
    return path.name


# ---------------------------------------------------------------------------
# Process utilities
# ---------------------------------------------------------------------------


def is_process_running(pid: int) -> bool:
    """Return True if a process with *pid* is currently running.

    Uses ``os.kill(pid, 0)`` which sends no signal but checks process existence.

    QA-007: Canonical implementation shared by update_index.py and vault_doctor.py.

    SEC-016: PermissionError now returns False. A PID we cannot signal
    belongs to another user, which under parsidion's single-user threat
    model means it is not one of our stale processes — the old True made
    a leftover ``pid: 1`` permanently block every PID-file singleton guard
    (doctor runs were stuck until the state file was hand-edited). Callers
    that need real mutual exclusion use ``vault_fs.try_singleton_lock``
    (flock), not this probe.

    Args:
        pid: Process ID to check.

    Returns:
        True if the process is running and signalable by us.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Cannot signal -> not ours -> report not running (SEC-016).
        return False


# ---------------------------------------------------------------------------
# Transcript analysis helpers (shared by session_stop and subagent_stop hooks)
# ---------------------------------------------------------------------------

TRANSCRIPT_CATEGORIES: dict[str, list[str]] = {
    "error_fix": [
        "fixed",
        "the issue was",
        "root cause",
        "the error",
        "resolved by",
        "the fix",
        "bug was",
        "problem was",
        "workaround",
    ],
    "research": [
        "found that",
        "documentation says",
        "according to",
        "turns out",
        "discovered that",
        "learned that",
        "it appears",
        "the docs say",
        "the spec says",
    ],
    "pattern": [
        "pattern",
        "approach",
        "technique",
        "best practice",
        "convention",
        "idiom",
        "architecture",
        "design decision",
    ],
    "config_setup": [
        "configured",
        "installed",
        "set up",
        "added to",
        "created",
        "initialized",
        "migrated",
        "deployed",
    ],
}


def parse_transcript_lines(lines: list[str]) -> list[str]:
    """Parse JSONL transcript lines and extract assistant message text.

    Supports both Claude Code transcript events (``type: assistant``) and
    pi transcript events (``type: message`` with ``message.role=assistant``).

    Args:
        lines: Raw JSONL lines from the transcript file.

    Returns:
        A list of text strings from assistant messages.
    """
    assistant_texts: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        role: str | None = None
        content: object = None

        message = entry.get("message")
        if isinstance(message, dict):
            role_raw = message.get("role")
            if isinstance(role_raw, str):
                role = role_raw
            content = message.get("content")

        if role is None:
            msg_type = entry.get("type")
            if isinstance(msg_type, str) and msg_type in {"assistant", "user"}:
                role = msg_type
                content = entry.get("content")

        if role != "assistant" or content is None:
            continue

        text = extract_text_from_content(content)
        if text.strip():
            assistant_texts.append(text)

    return assistant_texts


def parse_codex_transcript_lines(lines: list[str]) -> list[str]:
    """Parse Codex rollout JSONL lines and extract assistant message text."""
    texts: list[str] = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue

        item = None
        if isinstance(record, dict):
            item = record.get("payload")
            if not isinstance(item, dict):
                item = record.get("item")
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


def _extract_antigravity_parts(parts: object) -> str:
    """Extract text from Gemini ``parts`` arrays or string-like fields."""
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


def _extract_antigravity_content(content: object) -> str:
    """Extract assistant/model text from Gemini content shapes."""
    text = extract_text_from_content(content).strip()
    if text:
        return text
    return _extract_antigravity_parts(content)


def parse_antigravity_transcript_lines(lines: list[str]) -> list[str]:
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
        if isinstance(message, dict) and message.get("role") in {"model", "assistant"}:
            text = _extract_antigravity_content(message.get("content"))
            if text:
                texts.append(text)
            continue

        role = record.get("role")
        record_type = record.get("type")
        if role in {"model", "assistant"} or record_type in {"model", "assistant"}:
            text = _extract_antigravity_content(record.get("content"))
            if text:
                texts.append(text)
            continue

        llm_response = record.get("llm_response")
        if not isinstance(llm_response, dict):
            continue
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
            text = _extract_antigravity_parts(content.get("parts"))
            if text:
                texts.append(text)

    return texts


def detect_categories(texts: list[str]) -> dict[str, list[str]]:
    """Scan assistant texts for learnable content using keyword heuristics.

    Args:
        texts: List of assistant message texts.

    Returns:
        Dict mapping category keys to lists of matching text excerpts
        (each truncated to 500 chars).
    """
    found: dict[str, list[str]] = {}

    for text in texts:
        text_lower = text.lower()
        for category, keywords in TRANSCRIPT_CATEGORIES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    if category not in found:
                        found[category] = []
                    excerpt = text[:500].strip()
                    if excerpt and excerpt not in found[category]:
                        found[category].append(excerpt)
                    break

    return found
