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

# ARC-004: ``agent_adapter`` owns the per-runtime event->script-filename maps.
# It is on ``sys.path`` via ``installer/__init__.py`` (which inserts the
# skill's ``scripts/`` directory before any installer submodule body runs).
# Importing it here lets installer/paths.py re-export the maps under their
# historical installer-side names so existing call sites continue to work.
import agent_adapter as _agent_adapter
from core.vault_path import get_vaults_config_path, read_vaults_yaml

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
PARSIDION_VAULT_MD_SRC: Path = REPO_ROOT / "PARSIDION-VAULT.md"
# Pre-rename installs (<= 0.13.x) shipped CLAUDE-VAULT.md; cleaned up on reinstall.
LEGACY_CLAUDE_VAULT_MD = "CLAUDE-VAULT.md"
AGENT_INSTRUCTIONS_SRC: Path = (
    REPO_ROOT / "skills" / "parsidion" / "AGENT_INSTRUCTIONS.md"
)

# Hook script filenames installed inside the skill.
# ARC-004: the per-runtime event->script maps live in ``agent_adapter``
# (the canonical registry) and are re-exported here for back-compat with
# installer/hooks.py and install.py. The Claude map is exposed as
# ``_HOOK_SCRIPTS`` (its installer-side historical name); the canonical
# name ``_CLAUDE_HOOK_SCRIPTS`` is also available on ``agent_adapter``.
# ``tests/test_agent_adapter.py::TestHookScriptMapsSingleSource`` asserts
# these are the same object so drift cannot be reintroduced.
_HOOK_SCRIPTS: dict[str, str] = _agent_adapter._CLAUDE_HOOK_SCRIPTS
_CODEX_HOOK_SCRIPTS: dict[str, str] = _agent_adapter._CODEX_HOOK_SCRIPTS
_ANTIGRAVITY_HOOK_SCRIPTS: dict[str, str] = _agent_adapter._ANTIGRAVITY_HOOK_SCRIPTS
_ANTIGRAVITY_HOOK_NAMES: dict[str, str] = _agent_adapter._ANTIGRAVITY_HOOK_NAMES

# Per-event hook options merged into the hook handler entry in settings.json.
# Installer-only data — no equivalent on AgentAdapter today, so this stays
# here (the only place it is defined).
_HOOK_OPTIONS: dict[str, dict] = {
    "SubagentStop": {"async": True},
    "SessionEnd": {"async": True},
    # SessionStart runs vault retrieval plus (optionally) an AI selector
    # prompt — headless CLI backends run 8-40s — so it gets the same 60s
    # budget the codex (60s) and omp/pi (60s) runtimes allow. Kept here so
    # the merge path raises existing 10s installs to it on reinstall.
    "SessionStart": {"timeout": 60000},
}

_RUNTIME_CHOICES = ("claude", "codex", "antigravity", "both", "all", "none")


def _wants_claude_runtime(runtime: str) -> bool:
    """Return True when Claude integration is included in *runtime*."""
    return runtime in {"claude", "both", "all"}


def _wants_codex_runtime(runtime: str) -> bool:
    """Return True when Codex integration is included in *runtime*."""
    return runtime in {"codex", "both", "all"}


def _wants_antigravity_runtime(runtime: str) -> bool:
    """Return True when Antigravity integration is included in *runtime*."""
    return runtime in {"antigravity", "all"}


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
    # vault_path.VAULT_DIRS and expects installer to follow). The scripts dir
    # is already on sys.path — installer/__init__.py inserts it at package
    # import (ARC-008 removed this duplicate insert).
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


def validate_vault_path(raw: str) -> tuple[Path, str | None]:
    """Expand and validate the vault path.

    Returns:
        (resolved_path, error_message) — error is None when valid.

    ARC-002: lives next to ``_FORBIDDEN_PREFIXES`` so tests patch the source
    binding (``monkeypatch.setattr(installer.paths, "_FORBIDDEN_PREFIXES", …)``)
    rather than a re-export on ``install.py``'s namespace.
    """
    if not raw.strip():
        return Path(), "Path cannot be empty."

    expanded = Path(raw).expanduser().resolve()

    # SEC-009: Path.is_relative_to() prevents false positives where a forbidden
    # prefix string matches a different path (e.g. "/usr" matching "/usrdata").
    for forbidden in _FORBIDDEN_PREFIXES:
        forbidden_path = Path(forbidden).resolve()
        if expanded == forbidden_path or expanded.is_relative_to(forbidden_path):
            return expanded, f"Cannot use system or Claude config directory: {expanded}"

    return expanded, None


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
    config_path = get_vaults_config_path()
    # QA-004: shared reader from core.vault_path instead of a third
    # hand-rolled vaults.yaml parse.
    named, default_ref = read_vaults_yaml(config_path)

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
