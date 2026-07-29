"""Path constants and resolution helpers for the Parsidion installer.

All vault-path logic lives here: VAULT_DIRS extraction, default path
resolution, and uninstall-time vault root resolution.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import re
import sys
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
    REPO_ROOT / "agents" / "vault-deduplicator.md",
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
    "SubagentStop": "codex_subagent_stop_hook.py",
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
# VAULT_DIRS — imported from vault_path.py (canonical source) with regex fallback
# ---------------------------------------------------------------------------

# Hardcoded fallback used only when neither the import nor the regex can
# resolve the canonical list.Drift between this and vault_path.VAULT_DIRS
# is caught by tests/test_vault_dirs_sync.py.
_VAULT_DIRS_FALLBACK: list[str] = [
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


def _extract_vault_dirs() -> list[str]:
    """Return the canonical VAULT_DIRS list from vault_path.py.

    ARC-005: previously this function regex-parsed vault_common.py, but the
    definition moved to vault_path.py during the module split, leaving the
    regex matching nothing and silently returning the hardcoded fallback.
    The preferred path is now a direct import (vault_path is stdlib-only,
    same as the installer); the regex against vault_path.py remains as a
    defensive fallback for environments where the import fails (e.g. a
    stale or partial checkout) and is now loud — it warns on stderr rather
    than silently returning the fallback.
    """
    scripts_dir = SKILL_SRC / "scripts"
    # Prefer importing vault_path directly so installer.VAULT_DIRS tracks
    # runtime mutations of vault_path.VAULT_DIRS (the mechanism test patches
    # vault_path.VAULT_DIRS and expects installer to follow).
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import vault_path as _vault_path  # type: ignore[import-not-found]

        return list(_vault_path.VAULT_DIRS)
    except Exception as e:  # noqa: BLE001 — fall back to regex on any failure
        print(
            f"installer/paths.py: could not import vault_path.VAULT_DIRS ({e!r}); "
            "falling back to regex parse",
            file=sys.stderr,
        )

    # Regex fallback: parse vault_path.py source (the canonical definition
    # lives there now, not in vault_common.py).
    source_path = scripts_dir / "vault_path.py"
    try:
        text = source_path.read_text(encoding="utf-8")
    except OSError as e:
        print(
            f"installer/paths.py: could not read {source_path} ({e!r}); "
            "using hardcoded fallback",
            file=sys.stderr,
        )
        return list(_VAULT_DIRS_FALLBACK)
    m = re.search(
        r"^VAULT_DIRS:\s*list\[str\]\s*=\s*\[(.*?)\]",
        text,
        re.DOTALL | re.MULTILINE,
    )
    if not m:
        print(
            f"installer/paths.py: VAULT_DIRS regex did not match {source_path}; "
            "using hardcoded fallback",
            file=sys.stderr,
        )
        return list(_VAULT_DIRS_FALLBACK)
    dirs = re.findall(r'"([^"]+)"', m.group(1))
    if not dirs:
        print(
            f"installer/paths.py: VAULT_DIRS regex matched no entries in {source_path}; "
            "using hardcoded fallback",
            file=sys.stderr,
        )
        return list(_VAULT_DIRS_FALLBACK)
    return dirs


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

    ARC-019: repointed at ``~/.config/parsidion/vaults.yaml``. The previous
    implementation parsed a ``vault_root:`` key from the default vault's
    ``config.yaml`` — but nothing in the repo ever *wrote* that key, so the
    branch was unreachable and uninstall always fell back to the default
    vault root. ``record_installed_vault`` (the install-time helper) now
    writes both a named entry and a ``default:`` line into vaults.yaml
    whenever ``install.py --vault /custom/path`` is used, so reading the
    ``default:`` here finds what install put down.

    Resolution order:
      1. ``default:`` line in vaults.yaml (set by ``record_installed_vault``)
      2. First named entry in vaults.yaml (fallback if only ``vaults:`` set)
      3. ``~/ParsidionVault`` (or legacy ``~/ClaudeVault`` if it exists)
    """
    default = _default_vault_path()
    config_path = Path.home() / ".config" / PROJECT_NAME / "vaults.yaml"
    if not config_path.exists():
        return default
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return default

    named: dict[str, str] = {}
    default_ref = ""
    in_vaults = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "vaults:" or stripped.startswith("vaults:"):
            in_vaults = True
            continue
        if in_vaults and line and not line[0].isspace() and ":" in stripped:
            in_vaults = False
        if in_vaults and ":" in stripped:
            name, _, rest = stripped.partition(":")
            name = name.strip().strip("'\"")
            rest = rest.strip().strip("'\"")
            if name and rest:
                named[name] = rest
        if stripped.startswith("default:") and not in_vaults:
            default_ref = stripped.split(":", 1)[1].strip().strip("'\"")

    if default_ref:
        try:
            resolved = Path(default_ref).expanduser().resolve()
            if resolved.exists():
                return resolved
        except OSError:
            pass
    for path_str in named.values():
        try:
            resolved = Path(path_str).expanduser().resolve()
            if resolved.exists():
                return resolved
        except OSError:
            continue
    return default
