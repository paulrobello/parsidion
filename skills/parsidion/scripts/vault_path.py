"""Path resolution, validation, and directory constants for the Parsidion vault.

Handles vault resolution (multi-vault support), template directory lookup,
embeddings DB path, secure log directories, and log rotation.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import functools
import os
import sys
from pathlib import Path

__all__: list[str] = [
    # Constants
    "VAULT_ROOT",
    "TEMPLATES_DIR",
    "SCRIPTS_DIR",
    "VAULT_DIRS",
    "EXCLUDE_DIRS",
    "EMBEDDINGS_DB_FILENAME",
    # Secure logging
    "secure_log_dir",
    "rotate_log_file",
    # Vault resolver
    "VaultConfigError",
    "get_vaults_config_path",
    "list_named_vaults",
    "resolve_vault",
    "resolve_templates_dir",
    "get_embeddings_db_path",
    # Internal (re-exported for backward compat)
    "_VAULT_FORBIDDEN_PREFIXES",
    "_validate_vault_path",
    "_resolve_vault_cached",
]

# Default paths -- used as fallbacks by resolve_vault() and resolve_templates_dir().
# These are no longer patched by the installer (ARC-001 fix).  All code should call
# resolve_vault() or resolve_templates_dir() instead of using these directly.
# Kept as module-level constants for backward compatibility with external callers
# (e.g. parsidion-mcp, tests) that read vault_common.VAULT_ROOT.
VAULT_ROOT: Path = Path.home() / "ClaudeVault"
TEMPLATES_DIR: Path = Path.home() / ".claude" / "skills" / "parsidion" / "templates"
SCRIPTS_DIR: Path = Path.home() / ".claude" / "skills" / "parsidion" / "scripts"

VAULT_DIRS: list[str] = [
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
EXCLUDE_DIRS: set[str] = {".obsidian", "Templates", ".git", ".trash", "TagsRoutes"}

EMBEDDINGS_DB_FILENAME: str = "embeddings.db"

# Maximum lines kept in hook error log files before rotation.
_HOOK_ERROR_LOG_MAX_LINES: int = 2000


def secure_log_dir() -> Path:
    """Return ``~/.claude/logs/``, creating it with mode 0o700 if absent.

    Returns:
        Absolute Path to the secure log directory.
    """
    log_dir = Path.home() / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return log_dir


def rotate_log_file(log_path: Path, max_lines: int = _HOOK_ERROR_LOG_MAX_LINES) -> None:
    """Rotate a log file when it exceeds *max_lines*, keeping the second half.

    Best-effort -- never raises.

    Args:
        log_path: Path to the log file to rotate.
        max_lines: Maximum number of lines before rotation is triggered.
    """
    try:
        if not log_path.exists():
            return
        lines = log_path.read_text(encoding="utf-8").splitlines(keepends=True)
        if len(lines) <= max_lines:
            return
        keep = lines[max_lines // 2 :]
        log_path.write_text("".join(keep), encoding="utf-8")
    except OSError:
        pass


class VaultConfigError(Exception):
    """Raised when vault configuration is invalid."""

    pass


# -----------------------------------------------------------------------------
# Vault Resolver (multi-vault support)
# -----------------------------------------------------------------------------


def get_vaults_config_path() -> Path:
    """Return the path to the vaults configuration file.

    Uses XDG config home with fallback to legacy pre-rebrand locations.

    Returns:
        Path to vaults.yaml configuration file.
    """
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    config_base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    config_dir = config_base / "parsidion"

    # Fallback to legacy pre-rebrand locations if the new XDG dir does not exist.
    if not config_dir.exists():
        legacy_name = "parsidion" + "-cc"
        legacy_candidates = [config_base / legacy_name, Path.home() / f".{legacy_name}"]
        for legacy_dir in legacy_candidates:
            if legacy_dir.exists():
                config_dir = legacy_dir
                break

    return config_dir / "vaults.yaml"


def list_named_vaults() -> dict[str, Path]:
    """Load named vaults from vaults.yaml configuration.

    Parses a simple YAML file with top-level 'vaults:' key containing
    name-to-path mappings.

    Returns:
        Dictionary mapping vault names to their absolute paths.
        Empty dict if config file doesn't exist or has no vaults section.
    """
    config_path = get_vaults_config_path()
    if not config_path.exists():
        return {}

    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    vaults: dict[str, Path] = {}
    in_vaults_section = False

    for line in content.splitlines():
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            continue

        # Detect vaults section start
        if stripped == "vaults:" or stripped.startswith("vaults:"):
            in_vaults_section = True
            continue

        # Detect end of vaults section (new top-level key)
        if (
            in_vaults_section
            and stripped
            and not stripped.startswith("-")
            and ":" in stripped
        ):
            # Check if this is a new top-level key (no leading spaces)
            if line and not line[0].isspace():
                break

        # Parse vault entries
        if in_vaults_section and ":" in stripped:
            # Handle both "name: path" and "name:" formats
            parts = stripped.split(":", 1)
            if len(parts) == 2:
                name = parts[0].strip().strip('"').strip("'")
                path_str = parts[1].strip().strip('"').strip("'")
                if name and path_str:
                    vaults[name] = Path(path_str).expanduser().resolve()

    return vaults


# SEC-007: Forbidden vault path prefixes -- prevents resolve_vault() from
# pointing the vault into system directories or the Claude config tree.
# A subset of install.py's _FORBIDDEN_PREFIXES -- excludes /var and /tmp
# because on macOS pytest's tmp_path resolves to /private/var/... and
# these are legitimate for tests and transient vaults.
_VAULT_FORBIDDEN_PREFIXES: tuple[str, ...] = (
    str(Path.home() / ".claude"),
    "/System",
    "/usr",
    "/bin",
    "/sbin",
    "/etc",
    str(Path.home() / "Library"),
)


def _validate_vault_path(resolved: Path) -> None:
    """Raise VaultConfigError if *resolved* falls under a forbidden prefix.

    Args:
        resolved: Fully resolved vault path to validate.

    Raises:
        VaultConfigError: If the path is under a forbidden prefix.
    """
    for prefix in _VAULT_FORBIDDEN_PREFIXES:
        forbidden = Path(prefix).resolve()
        if resolved == forbidden or resolved.is_relative_to(forbidden):
            raise VaultConfigError(
                f"Vault path resolves to a forbidden location: {resolved}"
            )


def _resolve_vault_reference(reference: str) -> Path:
    """Resolve a vault reference (name or path) to an absolute Path.

    Args:
        reference: Either a vault name from vaults.yaml or an absolute/relative path.

    Returns:
        Absolute Path to the vault directory.

    Raises:
        VaultConfigError: If reference is a name that doesn't exist in vaults.yaml,
            or if the resolved path falls under a forbidden prefix.
    """
    # First, try as a path
    ref_path = Path(reference).expanduser()
    if ref_path.is_absolute() or ref_path.exists():
        resolved = ref_path.resolve()
        _validate_vault_path(resolved)
        return resolved

    # If not a valid path, look up by name
    named_vaults = list_named_vaults()
    if reference in named_vaults:
        resolved = named_vaults[reference]
        _validate_vault_path(resolved)
        return resolved

    # Not found
    raise VaultConfigError(
        f"Vault '{reference}' not found in {get_vaults_config_path()}. "
        f"Available vaults: {', '.join(named_vaults.keys()) or '(none configured)'}"
    )


def resolve_vault(
    explicit: str | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Resolve which vault to use based on precedence order.

    QA-012: This logic is duplicated in ``visualizer/lib/vaultResolver.ts``.
    Both implementations must stay in sync until vault resolution is served
    through the parsidion-mcp server (long-term plan).

    Precedence (highest to lowest):
    1. explicit flag (path or vault name)
    2. cwd/.claude/vault file (project-local vault)
    3. CLAUDE_VAULT environment variable
    4. Default ~/ClaudeVault

    Args:
        explicit: Optional explicit vault reference (name or path).
        cwd: Optional working directory for project-local vault lookup.
            If None, uses current working directory.

    Returns:
        Absolute Path to the resolved vault directory.

    Note:
        This function is cached with @functools.lru_cache(maxsize=8).
        The cache key is based on (explicit, normalized_cwd) arguments.
        ARC-009: ``cwd`` is normalized to a resolved ``str`` before the
        cache lookup so that ``Path("/x")`` and ``"/x"`` produce the
        same cache entry.
    """
    # ARC-009: Normalize cwd to a resolved str for consistent cache keys
    normalized_cwd: str | None = None
    if cwd is not None:
        normalized_cwd = str(Path(cwd).resolve())
    return _resolve_vault_cached(explicit, normalized_cwd)


# Expose cache_clear on the public function for backward compatibility.
# Tests and other callers use ``resolve_vault.cache_clear()`` to reset
# between test cases -- delegate to the inner cached function.
resolve_vault.cache_clear = lambda: _resolve_vault_cached.cache_clear()  # type: ignore[attr-defined]


@functools.lru_cache(maxsize=8)
def _resolve_vault_cached(
    explicit: str | None = None,
    cwd: str | None = None,
) -> Path:
    """Internal cached implementation -- call ``resolve_vault()`` instead."""
    # 1. Explicit flag takes highest precedence
    if explicit:
        return _resolve_vault_reference(explicit)

    # 2. Project-local vault (.claude/vault file)
    work_dir = Path(cwd) if cwd else Path.cwd()
    project_vault_file = work_dir / ".claude" / "vault"
    if project_vault_file.exists():
        try:
            vault_ref = project_vault_file.read_text(encoding="utf-8").strip()
            if vault_ref:
                return _resolve_vault_reference(vault_ref)
        except OSError:
            pass  # Fall through to next option

    # 3. Environment variable
    env_vault = os.environ.get("CLAUDE_VAULT")
    if env_vault:
        return _resolve_vault_reference(env_vault)

    # 4. Default vault
    # ARC-005: Check vault_common's VAULT_ROOT first so that
    # monkeypatch.setattr(vault_common, "VAULT_ROOT", ...) in tests
    # propagates correctly through the re-export facade.
    vc = sys.modules.get("vault_common")
    if vc is not None:
        return getattr(vc, "VAULT_ROOT", VAULT_ROOT)
    return VAULT_ROOT


def resolve_templates_dir() -> Path:
    """Resolve the templates directory path.

    Precedence (highest to lowest):
    1. CLAUDE_TEMPLATES_DIR environment variable
    2. Sibling ``templates/`` directory next to this script (works for both
       the repo source layout and the installed skill location)
    3. Default ``~/.claude/skills/parsidion/templates``

    Returns:
        Absolute Path to the templates directory.
    """
    # 1. Environment variable override
    env_templates = os.environ.get("CLAUDE_TEMPLATES_DIR")
    if env_templates:
        return Path(env_templates).expanduser().resolve()

    # 2. Sibling directory relative to this script
    script_dir = Path(__file__).resolve().parent
    sibling = script_dir.parent / "templates"
    if sibling.is_dir():
        return sibling

    # 3. Default
    return TEMPLATES_DIR


def get_embeddings_db_path(vault: Path | None = None) -> Path:
    """Return the path to the vault's embeddings database.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        Path to vault/embeddings.db.
    """
    vault = vault or resolve_vault()
    return vault / EMBEDDINGS_DB_FILENAME
