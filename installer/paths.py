"""Path constants and resolution helpers for the Parsidion installer.

All vault-path logic lives here: VAULT_DIRS extraction, default path
resolution, and uninstall-time vault root resolution.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Source layout (relative to the install script at repo root)
# ---------------------------------------------------------------------------

PROJECT_NAME = "parsidion"
LEGACY_PROJECT_NAME = "parsidion-cc"
SKILL_NAME = PROJECT_NAME
LEGACY_SKILL_NAME = LEGACY_PROJECT_NAME
DEFAULT_VAULT_NAME = "ParsidionVault"
LEGACY_DEFAULT_VAULT_NAME = "ClaudeVault"

REPO_ROOT: Path = Path(__file__).parent.parent.resolve()
SKILL_SRC: Path = REPO_ROOT / "skills" / SKILL_NAME
LEGACY_SKILL_SRC: Path = REPO_ROOT / "skills" / LEGACY_SKILL_NAME
AGENT_SRCS: list[Path] = [
    REPO_ROOT / "agents" / "research-agent.md",
    REPO_ROOT / "agents" / "vault-explorer.md",
    REPO_ROOT / "agents" / "project-explorer.md",
]
SCRIPTS_SRC: Path = REPO_ROOT / "scripts"
CLAUDE_VAULT_MD_SRC: Path = REPO_ROOT / "CLAUDE-VAULT.md"
AGENT_INSTRUCTIONS_SRC: Path = (
    REPO_ROOT / "skills" / "parsidion" / "AGENT_INSTRUCTIONS.md"
)

# Hook script filenames installed inside the skill.
# SessionEnd uses a shell wrapper that outputs {} immediately and runs the
# real hook detached — prevents "Hook cancelled" when Claude Code exits fast.
_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "session_start_hook.py",
    "SessionEnd": "session_stop_wrapper.sh",
    "PreCompact": "pre_compact_hook.py",
    "PostCompact": "post_compact_hook.py",
    "SubagentStop": "subagent_stop_hook.py",
}

# Per-event hook options merged into the hook handler entry in settings.json.
_HOOK_OPTIONS: dict[str, dict] = {
    "SubagentStop": {"async": True},
    "SessionEnd": {"async": True},
}

_CODEX_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "codex_session_start_hook.py",
    "Stop": "codex_stop_hook.py",
}

_GEMINI_HOOK_SCRIPTS: dict[str, str] = {
    "SessionStart": "gemini_session_start_hook.py",
    "SessionEnd": "gemini_session_end_hook.py",
}

_GEMINI_HOOK_NAMES: dict[str, str] = {
    "SessionStart": "parsidion-session-start",
    "SessionEnd": "parsidion-session-end",
}

_RUNTIME_CHOICES = ("claude", "codex", "gemini", "both", "all", "none")


def _wants_claude_runtime(runtime: str) -> bool:
    """Return True when Claude integration is included in *runtime*."""
    return runtime in {"claude", "both", "all"}


def _wants_codex_runtime(runtime: str) -> bool:
    """Return True when Codex integration is included in *runtime*."""
    return runtime in {"codex", "both", "all"}


def _wants_gemini_runtime(runtime: str) -> bool:
    """Return True when Gemini integration is included in *runtime*."""
    return runtime in {"gemini", "all"}


# ---------------------------------------------------------------------------
# VAULT_DIRS — parsed from vault_common.py to stay in sync
# ---------------------------------------------------------------------------


def _extract_vault_dirs() -> list[str]:
    """Parse VAULT_DIRS from vault_common.py source code.

    Uses a regex to find the ``VAULT_DIRS: list[str] = [...]`` assignment
    in the canonical source file.  Falls back to a hardcoded list if the
    parse fails (should never happen in a correct checkout).
    """
    source_path = SKILL_SRC / "scripts" / "vault_common.py"
    fallback = [
        "Daily",
        "Projects",
        "Languages",
        "Frameworks",
        "Patterns",
        "Debugging",
        "Tools",
        "Research",
        "Knowledge",
        "Templates",
        "History",
    ]
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError:
        return fallback
    m = re.search(
        r"^VAULT_DIRS:\s*list\[str\]\s*=\s*\[(.*?)\]",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not m:
        return fallback
    dirs = re.findall(r'"([^"]+)"', m.group(1))
    return dirs if dirs else fallback


VAULT_DIRS: list[str] = _extract_vault_dirs()

# ---------------------------------------------------------------------------
# Forbidden vault path prefixes (security guard)
# ---------------------------------------------------------------------------

_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    str(Path.home() / ".claude"),
    # Unix system directories
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    "/var",
    "/tmp",
    str(Path.home() / "Library"),
    # Windows system directories
    str(Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))),
    str(Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))),
    str(Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))),
    str(Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\Windows")),
)

# ---------------------------------------------------------------------------
# Default vault path resolution
# ---------------------------------------------------------------------------


def _default_vault_path(home: Path | None = None) -> Path:
    """Return the default vault path while preserving legacy installs."""
    root = home or Path.home()
    current = root / DEFAULT_VAULT_NAME
    legacy = root / LEGACY_DEFAULT_VAULT_NAME
    if legacy.exists() and not current.exists():
        return legacy
    return current


def _resolve_vault_root_for_uninstall() -> Path:
    """Best-effort vault root resolution for uninstall (no args available).

    Checks the default vault's ``config.yaml`` first, then falls back to the
    default path (``~/ParsidionVault`` or legacy ``~/ClaudeVault`` if present).
    """
    default = _default_vault_path()
    config = default / "config.yaml"
    if not config.exists():
        return default
    try:
        for line in config.read_text(encoding="utf-8").splitlines():
            stripped = line.split("#", 1)[0].strip()
            if stripped.startswith("vault_root:"):
                val = stripped.split(":", 1)[1].strip().strip("'\"")
                if val:
                    return Path(val).expanduser().resolve()
    except OSError:
        pass
    return default
