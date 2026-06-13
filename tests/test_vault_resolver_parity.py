"""ARC-004: Parity test — Python and TypeScript vault forbidden-prefix lists must stay in sync.

Parses the TypeScript ``VAULT_FORBIDDEN_PREFIXES`` constant from
``visualizer/lib/vaultResolver.ts`` with a regex and compares the resolved
set of path-pattern strings against the Python ``vault_path._VAULT_FORBIDDEN_PREFIXES``
tuple.

Why text-level, not a TS runtime call:
    The test suite runs with ``uv run pytest`` — no Node/Bun runtime is
    available in CI.  A regex parse of the TS source is sufficient because
    both lists are static compile-time constants.  Any drift (add/remove a
    prefix in one side) will immediately break this test.

CI-enforceable: yes — added to the root ``pytest tests/`` invocation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate source files
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VAULT_PATH_PY = _REPO_ROOT / "skills" / "parsidion" / "scripts" / "vault_path.py"
_VAULT_RESOLVER_TS = _REPO_ROOT / "visualizer" / "lib" / "vaultResolver.ts"

# Make vault_path importable
_SCRIPTS_DIR = str(_REPO_ROOT / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import vault_path  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: parse TS VAULT_FORBIDDEN_PREFIXES
# ---------------------------------------------------------------------------


def _parse_ts_prefixes(ts_source: str) -> list[str]:
    """Extract string arguments from the ``VAULT_FORBIDDEN_PREFIXES`` array in the TS source.

    Matches entries of the form ``path.resolve(...)``, ``path.resolve(_home, '...')``,
    and ``path.resolve('/...')``.  Returns the *unresolved* string literals so
    they can be compared structurally against the Python source literals (both
    expand ``~`` / ``_home`` at runtime; we compare the raw template strings here).

    Args:
        ts_source: Full text of ``vaultResolver.ts``.

    Returns:
        List of raw string arguments found inside ``VAULT_FORBIDDEN_PREFIXES``.
    """
    # Extract the array body between the [ and ]
    m = re.search(
        r"const\s+VAULT_FORBIDDEN_PREFIXES\s*:\s*[^=]+=\s*\[(.*?)\]",
        ts_source,
        re.DOTALL,
    )
    assert m, "Could not find VAULT_FORBIDDEN_PREFIXES array in vaultResolver.ts"
    array_body = m.group(1)

    # Collect all single/double-quoted string literals inside path.resolve(...)
    # Each entry is like: path.resolve(_home, '.claude') or path.resolve('/System')
    raw_strings: list[str] = re.findall(r"['\"]([^'\"]+)['\"]", array_body)
    return raw_strings


def _normalize_py_prefixes(py_prefixes: tuple[str, ...]) -> list[str]:
    """Extract the path components (after ~/ or /) from the Python prefix strings.

    Python entries look like:
        str(Path.home() / ".claude")  -> evaluated to e.g. '/Users/x/.claude'
        "/System"
        str(Path.home() / "Library")  -> e.g. '/Users/x/Library'

    We normalize by extracting the basename/relative segment that is independent
    of the actual home directory, so we can compare the structure.

    Args:
        py_prefixes: The ``_VAULT_FORBIDDEN_PREFIXES`` tuple from ``vault_path``.

    Returns:
        Sorted list of normalized segments.
    """
    home = str(Path.home())
    result: list[str] = []
    for p in py_prefixes:
        if p.startswith(home + "/"):
            result.append(p[len(home) + 1 :])
        elif p.startswith("/"):
            result.append(p)
        else:
            result.append(p)
    return sorted(result)


def _normalize_ts_prefixes(raw_strings: list[str]) -> list[str]:
    """Extract the path components from the TS string literals.

    TS entries reference ``_home`` via a variable; the string literals are
    the bare path segments like ``.claude``, ``Library``, or absolute paths
    like ``/System``, ``/usr`` etc.

    Args:
        raw_strings: Raw quoted strings parsed from the TS ``path.resolve(...)`` calls.

    Returns:
        Sorted list of normalized segments.
    """
    result: list[str] = []
    for s in raw_strings:
        # Skip the '_home' variable name — it's not a path literal
        if s == "_home":
            continue
        result.append(s)
    return sorted(result)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVaultResolverParity:
    """ARC-004: Python _VAULT_FORBIDDEN_PREFIXES and TS VAULT_FORBIDDEN_PREFIXES must match."""

    def test_ts_file_exists(self) -> None:
        """vaultResolver.ts must exist at the expected location."""
        assert _VAULT_RESOLVER_TS.exists(), (
            f"vaultResolver.ts not found at {_VAULT_RESOLVER_TS}"
        )

    def test_python_prefixes_present(self) -> None:
        """vault_path._VAULT_FORBIDDEN_PREFIXES must be a non-empty tuple."""
        assert vault_path._VAULT_FORBIDDEN_PREFIXES, (
            "_VAULT_FORBIDDEN_PREFIXES is empty in vault_path.py"
        )

    def test_forbidden_prefix_lists_in_sync(self) -> None:
        """Python and TS forbidden-prefix lists must have the same path segments.

        Both lists are normalized to home-relative or absolute segments before
        comparison, so the test is independent of the actual home directory.
        If you add a prefix to one side, add it to the other and update this test.
        """
        ts_source = _VAULT_RESOLVER_TS.read_text(encoding="utf-8")
        raw_ts = _parse_ts_prefixes(ts_source)
        ts_segments = _normalize_ts_prefixes(raw_ts)
        py_segments = _normalize_py_prefixes(vault_path._VAULT_FORBIDDEN_PREFIXES)

        assert ts_segments == py_segments, (
            "ARC-004: VAULT_FORBIDDEN_PREFIXES mismatch between Python and TypeScript!\n"
            f"  Python   (vault_path.py):     {py_segments}\n"
            f"  TypeScript (vaultResolver.ts): {ts_segments}\n"
            "Update both files to keep them in sync."
        )

    def test_forbidden_prefix_count_matches(self) -> None:
        """Both lists must have the same number of entries."""
        ts_source = _VAULT_RESOLVER_TS.read_text(encoding="utf-8")
        raw_ts = _parse_ts_prefixes(ts_source)
        ts_segments = _normalize_ts_prefixes(raw_ts)
        py_segments = _normalize_py_prefixes(vault_path._VAULT_FORBIDDEN_PREFIXES)

        assert len(ts_segments) == len(py_segments), (
            f"Prefix count mismatch: Python has {len(py_segments)}, "
            f"TypeScript has {len(ts_segments)}."
        )

    def test_claude_config_dir_is_forbidden(self) -> None:
        """Both lists must forbid the ~/.claude directory."""
        ts_source = _VAULT_RESOLVER_TS.read_text(encoding="utf-8")
        raw_ts = _parse_ts_prefixes(ts_source)

        # Python check
        home = str(Path.home())
        py_claude = home + "/.claude"
        assert py_claude in vault_path._VAULT_FORBIDDEN_PREFIXES, (
            f"~/.claude ({py_claude}) not in Python _VAULT_FORBIDDEN_PREFIXES"
        )

        # TS check — the raw literal '.claude' must be present
        assert ".claude" in raw_ts, (
            "'.claude' segment not found in TS VAULT_FORBIDDEN_PREFIXES"
        )

    def test_system_paths_are_forbidden(self) -> None:
        """Both Python and TS lists must forbid core system paths."""
        required_absolute = ["/System", "/usr", "/bin", "/sbin", "/etc"]

        for path_str in required_absolute:
            assert path_str in vault_path._VAULT_FORBIDDEN_PREFIXES, (
                f"{path_str} missing from Python _VAULT_FORBIDDEN_PREFIXES"
            )

        ts_source = _VAULT_RESOLVER_TS.read_text(encoding="utf-8")
        raw_ts = _parse_ts_prefixes(ts_source)
        for path_str in required_absolute:
            assert path_str in raw_ts, (
                f"{path_str} missing from TS VAULT_FORBIDDEN_PREFIXES"
            )
