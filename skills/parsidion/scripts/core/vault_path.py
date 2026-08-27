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
from dataclasses import dataclass
from pathlib import Path

from .yaml_lite import split_key_value, strip_quote_edges

__all__: list[str] = [
    # Constants
    "VAULT_ROOT",
    "DEFAULT_VAULT_NAME",
    "LEGACY_DEFAULT_VAULT_NAME",
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
    "read_vaults_yaml",
    "render_vaults_yaml",
    "resolve_vault",
    "resolve_vault_server",
    "default_vault_root",
    "resolve_templates_dir",
    "get_embeddings_db_path",
    "is_symlink_inside_vault",
    "is_path_inside_vault",
    # Internal (re-exported for backward compat)
    "_VAULT_FORBIDDEN_PREFIXES",
    "_validate_vault_path",
    "_named_vault_paths",
    "_resolve_vault_cached",
]

# Default paths -- used as fallbacks by resolve_vault() and resolve_templates_dir().
# These are no longer patched by the installer (ARC-001 fix).  All code should call
# resolve_vault() or resolve_templates_dir() instead of using these directly.
# Kept as module-level constants for backward compatibility with external callers
# (e.g. parsidion-mcp, tests) that read vault_common.VAULT_ROOT.
DEFAULT_VAULT_NAME = "ParsidionVault"
LEGACY_DEFAULT_VAULT_NAME = "ClaudeVault"


def default_vault_root(home: Path | None = None) -> Path:
    """Return the default vault path, preserving legacy installs.

    New installs default to ``~/ParsidionVault``. If a user already has a
    legacy ``~/ClaudeVault`` and has not created ``~/ParsidionVault``, keep
    using the legacy path so upgrades do not silently create a second vault.
    """
    root = home or Path.home()
    current = root / DEFAULT_VAULT_NAME
    legacy = root / LEGACY_DEFAULT_VAULT_NAME
    if legacy.exists() and not current.exists():
        return legacy
    return current


VAULT_ROOT: Path = default_vault_root()
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

    SEC-110: ``Path.mkdir(mode=...)`` is ignored when the directory already
    exists, so the 0o700 intent never applied to a pre-existing ``~/.claude/logs``
    (typically created world-readable by ``session_stop_wrapper.sh``'s plain
    ``mkdir -p``). Re-chmod explicitly so an existing dir is repaired on every call.

    Returns:
        Absolute Path to the secure log directory.
    """
    log_dir = Path.home() / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(log_dir, 0o700)
    except OSError:
        pass
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


def read_vaults_yaml(path: Path | None = None) -> tuple[dict[str, str], str | None]:
    """Parse ``vaults.yaml`` into its named entries and ``default`` value.

    QA-004: the single ``vaults.yaml`` reader. The installer's record and
    uninstall paths previously carried two private copies of this parse; both
    now call this function, and ``list_named_vaults`` is built on it too.
    ENH-024: key/value splitting and quote stripping come from
    ``core/yaml_lite`` (``split_key_value`` + the reader's historical
    permissive ``strip_quote_edges``) so the tokenization rules are shared
    with the config and frontmatter parsers.

    Values are returned raw (no ``expanduser``/``resolve``) so writers can
    re-emit them verbatim; callers resolve when they need a real path.

    Args:
        path: Config file to read. Defaults to ``get_vaults_config_path()``.

    Returns:
        ``(vaults, default)`` where *vaults* maps entry names to their raw
        path strings and *default* is the raw ``default:`` value or None.
        ``({}, None)`` when the file is missing or unreadable.
    """
    config_path = path if path is not None else get_vaults_config_path()
    if not config_path.exists():
        return {}, None
    try:
        content = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}, None

    vaults: dict[str, str] = {}
    default: str | None = None
    in_vaults = False
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "vaults:" or stripped.startswith("vaults:"):
            in_vaults = True
            continue
        key_value = split_key_value(stripped)
        if key_value is None:
            # No colon: neither an entry line nor a section-exit candidate.
            continue
        key, raw_value = key_value
        # End of vaults: section on a new top-level key.
        if in_vaults and line and not line[0].isspace():
            in_vaults = False
        if in_vaults:
            name = strip_quote_edges(key)
            value = strip_quote_edges(raw_value)
            if name and value:
                vaults[name] = value
        if not in_vaults and stripped.startswith("default:"):
            default = strip_quote_edges(raw_value) or None
    return vaults, default


# QA-104 structured model for vaults.yaml rendering. Parse keeps every
# source line verbatim as a node tagged with its role; mutation rewrites
# only the managed lines (the named entry and ``default:``); serialization
# is a join -- so preservation of comments, blank lines, and unknown keys
# is structural, not emergent from a line-oriented rewrite.
_ROLE_RAW = "raw"
_ROLE_HEADER = "header"
_ROLE_ENTRY = "entry"
_ROLE_DEFAULT = "default"


@dataclass
class _VaultsNode:
    """One line of a vaults.yaml file with its parsed role."""

    text: str
    role: str
    key: str = ""


@dataclass
class _VaultsFile:
    """Ordered model of a vaults.yaml file.

    The node list is the file: entries and the ``default:`` line are found
    by role, everything else passes through to serialization untouched.
    """

    nodes: list[_VaultsNode]

    @property
    def has_section(self) -> bool:
        """Whether a column-0 ``vaults:`` line exists (the template gate)."""
        return any(
            node.role == _ROLE_HEADER and node.text.startswith("vaults:")
            for node in self.nodes
        )

    def set_entry(self, name: str, value: str) -> None:
        """Set the named vault entry inside the section, inserting once.

        An existing in-section slot is rewritten in place (a duplicated
        slot rewrites every copy, matching the historical behavior of
        rewriting every matching line). A missing slot is inserted once --
        directly under the section header for a brand-new name, or after
        the section's last non-blank line when the name already appears
        elsewhere in the file (e.g. shadowed by a top-level key).
        """
        found = False
        for node in self.nodes:
            if node.role == _ROLE_ENTRY and node.key == name:
                node.text = f"  {name}: {value}"
                found = True
        if found:
            return
        self.nodes.insert(
            self._entry_insertion_index(name),
            _VaultsNode(f"  {name}: {value}", _ROLE_ENTRY, name),
        )

    def set_default(self, value: str) -> None:
        """Replace every top-level ``default:`` line; append one when absent."""
        found = False
        for node in self.nodes:
            if node.role == _ROLE_DEFAULT:
                node.text = f"default: {value}"
                found = True
        if not found:
            self.nodes.append(
                _VaultsNode(f"default: {value}", _ROLE_DEFAULT, "default")
            )

    def serialize(self) -> str:
        """Emit the model with exactly one trailing newline."""
        return "\n".join([*(node.text for node in self.nodes), ""])

    def _entry_insertion_index(self, name: str) -> int:
        span = self._section_span()
        if span is None:
            return len(self.nodes)
        header, end = span
        if not any(node.text.strip().startswith(f"{name}:") for node in self.nodes):
            # Brand-new name: directly under the section header.
            return header + 1
        # Name shadowed elsewhere in the file: keep the section's existing
        # content first, insert after its last non-blank line.
        for i in range(end - 1, header, -1):
            if self.nodes[i].text.strip():
                return i + 1
        return header + 1

    def _section_span(self) -> tuple[int, int] | None:
        """Node index range ``[header, end)`` of the first vaults: section."""
        header = next(
            (i for i, node in enumerate(self.nodes) if node.role == _ROLE_HEADER),
            None,
        )
        if header is None:
            return None
        end = len(self.nodes)
        for i in range(header + 1, len(self.nodes)):
            line = self.nodes[i].text
            if line and not line[0].isspace():
                end = i
                break
        return header, end


def _parse_vaults_file(text: str) -> _VaultsFile:
    """Parse vaults.yaml text into the ordered QA-104 model.

    Line semantics mirror the reader/writer contract: a line whose stripped
    form starts with ``vaults:`` (any indent) arms the section, and the
    first following non-blank column-0 line ends it. Entry lines are the
    indented non-comment ``key: value`` lines inside the section. Keys are
    kept RAW (no strip, no quote handling -- unlike the reader's yaml_lite
    tokenization) because ``set_entry`` matches them verbatim for in-place
    rewrites.
    """
    nodes: list[_VaultsNode] = []
    in_vaults = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("vaults:"):
            nodes.append(_VaultsNode(line, _ROLE_HEADER, "vaults"))
            in_vaults = True
            continue
        if in_vaults and line and not line[0].isspace():
            in_vaults = False
        if in_vaults and stripped and not stripped.startswith("#") and ":" in stripped:
            nodes.append(_VaultsNode(line, _ROLE_ENTRY, stripped.partition(":")[0]))
            continue
        if not in_vaults and stripped.startswith("default:"):
            nodes.append(_VaultsNode(line, _ROLE_DEFAULT, "default"))
            continue
        nodes.append(_VaultsNode(line, _ROLE_RAW, ""))
    return _VaultsFile(nodes)


def _template_lines(
    vaults: dict[str, str], vault_name: str, vault_path_str: str
) -> list[str]:
    """Build a minimal vaults.yaml body for a file with no usable section."""
    entries = dict(vaults)
    entries[vault_name] = vault_path_str
    lines = [
        "# Named vaults for parsidion",
        "# Populated by `install.py --vault` (ARC-019).",
        "",
        "vaults:",
    ]
    lines.extend(f"  {name}: {path}" for name, path in entries.items())
    lines.append("")
    lines.append(f"default: {vault_path_str}")
    return lines


def render_vaults_yaml(
    vaults: dict[str, str],
    default: str,
    vault_name: str,
    vault_path_str: str,
    original: str,
) -> str:
    """Return new vaults.yaml content with *vault_name* and ``default:`` set.

    QA-004: the single ``vaults.yaml`` writer body. QA-104: rewritten as
    parse -> mutate -> serialize over :class:`_VaultsFile`, so the named
    entry and the ``default:`` line are the only lines rewritten and the
    file's existing structure (comments, the ``vaults:`` section, other
    top-level keys) survives in order. Falls back to a fresh template when
    the file is empty or has no column-0 ``vaults:`` section. The output
    round-trips through ``read_vaults_yaml`` unchanged and re-rendering it
    is a byte-identical no-op. The caller's ``vaults`` dict is not
    modified; *default* is accepted for signature compatibility and the
    new default is always *vault_path_str*.
    """
    model = _parse_vaults_file(original)
    if not model.has_section:
        return "\n".join([*_template_lines(vaults, vault_name, vault_path_str), ""])
    model.set_entry(vault_name, vault_path_str)
    model.set_default(vault_path_str)
    return model.serialize()


def list_named_vaults() -> dict[str, Path]:
    """Load named vaults from vaults.yaml configuration.

    Parses a simple YAML file with top-level 'vaults:' key containing
    name-to-path mappings.

    Returns:
        Dictionary mapping vault names to their absolute paths.
        Empty dict if config file doesn't exist or has no vaults section.
    """
    vaults, _default = read_vaults_yaml()
    return {
        name: Path(path_str).expanduser().resolve() for name, path_str in vaults.items()
    }


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


def is_symlink_inside_vault(path: Path, vault_root: Path) -> bool:
    """Return True if *path* is a symlink whose target stays inside *vault_root*.

    SEC-106: ``os.walk`` does not follow symlinked *directories* but it does
    list symlinked *files*, so a shared-vault committer can plant
    ``Patterns/evil.md -> ~/.ssh/id_ed25519`` and have the indexer read it.
    Non-symlinks are always considered safe. Symlinks whose ``resolve()``
    raises ``OSError`` or escapes *vault_root* are unsafe. Callers must
    ``resolve()`` *vault_root* once before the walk and pass the resolved
    value here, so the comparison is not defeated by a symlinked vault root.
    """
    if not path.is_symlink():
        return True
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(vault_root)


def is_path_inside_vault(path: Path, vault_root: Path) -> bool:
    """Return True if *path*'s resolved location is inside *vault_root*.

    SEC-130: consolidates the four hand-rolled
    ``path.resolve().is_relative_to(vault.resolve())`` checks that had
    been duplicated across ``vault_index``, ``session_start_hook``,
    ``summarize_sessions``, and ``vault_path``. Both *path* and
    *vault_root* are resolved before comparison, so a symlinked vault
    root or a symlinked target cannot defeat the check. ``OSError`` from
    ``resolve()`` (broken symlink, permission) returns False — callers
    should treat that as "outside" and refuse to read/write the path.

    For the symlink-specific ``is the symlink target safe to follow``
    question used by vault walks, prefer :func:`is_symlink_inside_vault`
    — it returns True for non-symlinks without paying for a ``resolve()``.
    """
    try:
        resolved = path.resolve()
        root_resolved = vault_root.resolve()
    except OSError:
        return False
    return resolved.is_relative_to(root_resolved)


def _named_vault_paths() -> set[Path]:
    """Return the set of resolved paths registered in ``vaults.yaml``.

    SEC-P001: backs the allowlist check in :func:`_resolve_vault_reference`.
    Mirrors the TypeScript ``loadAllowedVaults`` pattern -- a single source
    of truth for "which paths may a vault reference resolve to?"
    """
    try:
        return set(list_named_vaults().values())
    except OSError:
        return set()


def _resolve_vault_reference(reference: str) -> Path:
    """Resolve a vault reference (name or path) to an absolute Path.

    SEC-P001: Allowlist resolver. The reference must resolve to
    either (a) a named vault registered in ``vaults.yaml``, or (b) the
    default vault by its own path.  Arbitrary filesystem paths are
    rejected even when they do not fall under
    :data:`_VAULT_FORBIDDEN_PREFIXES`, closing the vector where a
    malicious repo's ``cwd/.claude/vault`` or a crafted ``CLAUDE_VAULT``
    could redirect vault writes to an attacker-chosen location. This is
    also the engine behind :func:`resolve_vault_server`, which the
    visualizer's TypeScript resolver now delegates to (ENH-009).

    The system-path guard (:func:`_validate_vault_path`) is retained as
    defense-in-depth for local ``vaults.yaml`` misconfiguration (e.g. a
    named vault registered to ``/etc``).

    Args:
        reference: Either a vault name from vaults.yaml or a vault path.

    Returns:
        Absolute Path to the resolved vault directory.

    Raises:
        VaultConfigError: If the reference is neither a named vault nor
            the default vault, or if the resolved path falls under a
            forbidden prefix.
    """
    named = list_named_vaults()

    # 1. Named-vault lookup (by name).
    if reference in named:
        resolved = named[reference].resolve()
        _validate_vault_path(resolved)
        return resolved

    # 2. Path reference -- accept only when the resolved path matches a
    #    registered named-vault path or the default vault path.  This is
    #    the allowlist: an arbitrary existing path (e.g. ~/.ssh or
    #    /tmp/evil) is rejected even though it is not under a forbidden
    #    prefix.
    ref_path = Path(reference).expanduser()
    try:
        candidate = ref_path.resolve()
    except OSError:
        candidate = ref_path
    default = default_vault_root()

    if candidate in _named_vault_paths() or candidate == default:
        _validate_vault_path(candidate)
        return candidate

    # 3. Not allowlisted.
    raise VaultConfigError(
        f"Vault '{reference}' is not a named vault in "
        f"{get_vaults_config_path()} and does not match the default "
        f"vault ({default}). "
        f"Available: {', '.join(named) or '(none configured)'}"
    )


def resolve_vault(
    explicit: str | None = None,
    cwd: str | Path | None = None,
) -> Path:
    """Resolve which vault to use based on precedence order.

    ENH-009 (resolved): the visualizer no longer reimplements this in
    TypeScript. Its ``vaultResolver.ts`` delegates to
    :func:`resolve_vault_server` (the deliberately narrower server contract)
    via the ``vault_resolve.py`` CLI, so the allowlist algorithm is
    single-sourced here. The narrower contract is formalized as
    :func:`resolve_vault_server` (named vaults + default + ``VAULT_ROOT``
    override; no ``cwd/.claude/vault`` or ``CLAUDE_VAULT``, because a
    long-lived server has no current project). The shared parity fixture
    (``tests/fixtures/parity/vault-resolution.json``) still pins the
    observable contract both the full and server resolvers must satisfy.

    Precedence (highest to lowest):
    1. explicit flag (path or vault name)
    2. cwd/.claude/vault file (project-local vault)
    3. CLAUDE_VAULT environment variable
    4. vaults.yaml top-level ``default:`` key (a vault name or a registered
       path; ARC-019), then the filesystem default ~/ParsidionVault, or
       legacy ~/ClaudeVault if it already exists

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
    # A Path explicit (e.g. doctor/cli.py's ``--vault`` uses ``type=Path``)
    # must be coerced to str before the named-vault dict lookup in
    # _resolve_vault_reference, which is keyed by vault NAME.
    if explicit is not None and not isinstance(explicit, str):
        explicit = str(explicit)
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
    # SEC-P001: .claude/vault is attacker-controlled (any repo can plant
    # one), so the reference must pass the allowlist check.  A reference
    # that resolves to a non-allowlisted path is silently skipped so a
    # malicious repo cannot crash the hook -- it simply falls through to
    # the next resolution branch instead of writing vault content into
    # the attacker-chosen location.
    work_dir = Path(cwd) if cwd else Path.cwd()
    project_vault_file = work_dir / ".claude" / "vault"
    if project_vault_file.exists():
        try:
            vault_ref = project_vault_file.read_text(encoding="utf-8").strip()
            if vault_ref:
                return _resolve_vault_reference(vault_ref)
        except (OSError, VaultConfigError):
            pass  # Fall through to next option

    # 3. Environment variable
    # SEC-P001: CLAUDE_VAULT is user-controlled but fall through to the
    # default on a non-allowlisted path rather than crashing the hook.
    env_vault = os.environ.get("CLAUDE_VAULT")
    if env_vault:
        try:
            return _resolve_vault_reference(env_vault)
        except VaultConfigError:
            pass  # Fall through to default

    # 4. Default vault
    # ARC-003: this branch is DEPRECATED. The previous production caller
    # (``update_index.py``) now threads ``args.vault`` as the ``explicit``
    # argument to :func:`resolve_vault`, so it no longer mutates
    # ``vault_common.VAULT_ROOT`` and no longer relies on this branch.
    # The check remains to support any external caller that still mutates
    # ``vault_common.VAULT_ROOT`` at runtime; mutations now emit a
    # ``DeprecationWarning`` so the next removal is signposted. Do not write
    # new code that depends on this — pass the vault path explicitly via
    # ``resolve_vault(explicit=...)`` instead.
    #
    # ARC-009 (still applies): Tests should NOT rely on this branch.  Use the
    # ``tmp_vault`` fixture in tests/conftest.py instead, which sets
    # CLAUDE_VAULT (branch 3 above) — the public override path.
    vc = sys.modules.get("vault_common")
    if vc is not None:
        vc_root = getattr(vc, "VAULT_ROOT", VAULT_ROOT)
        if Path(vc_root) != VAULT_ROOT:
            import warnings

            warnings.warn(
                "vault_common.VAULT_ROOT was mutated at runtime; "
                "resolve_vault() branch 4 is deprecated. Pass the vault "
                "path explicitly via resolve_vault(explicit=...) instead. "
                "See ARC-003.",
                DeprecationWarning,
                stacklevel=2,
            )
            return Path(vc_root)

    # 4b. Configured default: the vaults.yaml top-level ``default:`` key
    # (ARC-019). Resolved through the same SEC-P001 allowlist as any other
    # reference; an unset or unresolvable value falls through to the
    # filesystem default below.
    configured = _configured_default_vault()
    if configured is not None:
        return configured
    return default_vault_root()


def _configured_default_vault() -> Path | None:
    """Resolve the top-level ``default:`` key of ``vaults.yaml``, if usable.

    ARC-019: the value may be a vault name or a path, and must pass the same
    SEC-P001 allowlist as any other reference (:func:`_resolve_vault_reference`)
    -- a ``default:`` pointing at an unregistered or forbidden path is
    ignored, not honored. Returns None when the key is absent or fails
    resolution; callers fall back to the filesystem default.
    """
    _, default_ref = read_vaults_yaml()
    if not default_ref:
        return None
    try:
        return _resolve_vault_reference(default_ref)
    except VaultConfigError:
        return None


def _server_default_vault() -> Path:
    """Default vault for a non-project context (the visualizer, CLIs).

    Honors the ``VAULT_ROOT`` environment override -- the one default-vault
    override the long-lived visualizer server has historically supported
    (formerly TS ``getDefaultVault()``) -- then the vaults.yaml top-level
    ``default:`` key (ARC-019), then falls back to
    :func:`default_vault_root`. Unlike :func:`resolve_vault`, a server has no
    project context, so this never consults ``cwd/.claude/vault`` or
    ``CLAUDE_VAULT``.
    """
    env_root = os.environ.get("VAULT_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    configured = _configured_default_vault()
    if configured is not None:
        return configured
    return default_vault_root()


def resolve_vault_server(reference: str | None = None) -> Path:
    """Canonical vault resolver for non-project contexts.

    ENH-009: the single source of truth for the "server allowlist" contract.
    The visualizer's TypeScript ``vaultResolver`` delegates to this (via the
    ``vault_resolve.py`` CLI) instead of reimplementing the allowlist, so there
    is no longer a second implementation to drift (was QA-012 / ARC-007 /
    SEC-P001). ``resolveVault`` / ``getDefaultVault`` / ``listNamedVaults`` on
    the TS side are thin subprocess callers over this function.

    Resolution is an allowlist: *reference* must match either (a) a named vault
    registered in ``vaults.yaml`` or (b) the default vault by its own path.
    With no *reference* the default vault is returned -- honoring ``VAULT_ROOT``,
    then the vaults.yaml top-level ``default:`` key (ARC-019), then the
    filesystem default. The ``cwd/.claude/vault`` and ``CLAUDE_VAULT``
    channels of :func:`resolve_vault` are intentionally absent -- a long-lived
    server has no current project and no inherited runtime environment
    (ARC-007).

    Args:
        reference: Optional vault name or path. ``None``/empty resolves the
            default vault.

    Returns:
        Absolute :class:`~pathlib.Path` to the resolved vault directory.

    Raises:
        VaultConfigError: If *reference* is neither a named vault nor the
            default vault, or the resolved path falls under a forbidden prefix.
    """
    named = list_named_vaults()
    default = _server_default_vault()

    # Default vault (no reference).
    if not reference:
        try:
            resolved = default.expanduser().resolve()
        except OSError:
            resolved = default.expanduser()
        _validate_vault_path(resolved)
        return resolved

    # Named-vault lookup (by name).
    if reference in named:
        resolved = named[reference].resolve()
        _validate_vault_path(resolved)
        return resolved

    # Path reference -- allowlist: must match a registered named-vault path or
    # the default vault path. An arbitrary existing path is rejected even when
    # it is not under a forbidden prefix (SEC-P001).
    ref_path = Path(reference).expanduser()
    try:
        candidate = ref_path.resolve()
    except OSError:
        candidate = ref_path

    if candidate in _named_vault_paths() or candidate == default:
        _validate_vault_path(candidate)
        return candidate

    raise VaultConfigError(
        f"Vault '{reference}' is not a named vault in "
        f"{get_vaults_config_path()} and does not match the default "
        f"vault ({default}). "
        f"Available: {', '.join(named) or '(none configured)'}"
    )


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

    # 2. Sibling directory relative to this script. This module lives at
    #    scripts/core/vault_path.py, so templates/ is two parents up
    #    (.../scripts/core -> .../scripts -> .../parsidion/templates).
    script_dir = Path(__file__).resolve().parent
    sibling = script_dir.parent.parent / "templates"
    if sibling.is_dir():
        return sibling

    # 3. Default
    return TEMPLATES_DIR


def get_embeddings_db_path(vault: str | Path | None = None) -> Path:
    """Return the path to the vault's embeddings database.

    Args:
        vault: Optional vault path (str or Path). Defaults to resolve_vault().

    Returns:
        Path to vault/embeddings.db.
    """
    if isinstance(vault, str):  # be liberal: accept str paths from callers
        vault = Path(vault)
    vault = vault or resolve_vault()
    return vault / EMBEDDINGS_DB_FILENAME
