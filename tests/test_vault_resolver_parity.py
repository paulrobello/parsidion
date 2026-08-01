"""ENH-005: Python <-> TypeScript vault resolver parity.

Two layers of cross-language agreement, both enforced here:

1. ``VAULT_FORBIDDEN_PREFIXES`` -- the *list* of forbidden path prefixes must
   be byte-for-byte the same in Python (``vault_path._VAULT_FORBIDDEN_PREFIXES``)
   and TypeScript (``vaultResolver.ts``). Covered by source-text parsing in
   :class:`TestVaultForbiddenPrefixListParity`, because both are static
   compile-time constants and the Python test runner has no Node/Bun runtime.

2. Vault *resolution* vectors -- the observable behaviour of
   ``resolve_vault()`` (Python) and ``resolveVault()`` (TypeScript) against a
   shared vector set committed at
   ``tests/fixtures/parity/vault-resolution.json``. Both this file and
   ``visualizer/lib/vaultResolver.parity.test.ts`` load the SAME JSON, so
   adding a vector forces both sides to acknowledge it. Covered by
   :class:`TestVaultResolutionVectors`.

The two resolvers are not identical (see the fixture's ``$comment`` and the
``applies_to``-scoped vectors): Python is a 4-channel resolver
(explicit / cwd/.claude/vault / CLAUDE_VAULT / default); TypeScript is a
single-channel allowlist resolver (named vaults or the default path) with a
``VAULT_ROOT`` default override. SEC-P001 back-ported the TS allowlist to
Python's reference resolver, so arbitrary paths are now rejected on both
sides; Python's attacker-controlled channels (.claude/vault, CLAUDE_VAULT)
additionally fall through to the default on rejection rather than crashing
the hook. Where a channel genuinely only exists on one side, the vector
carries ``"applies_to": ["python"]`` / ``["typescript"]`` and this suite
asserts every vector is either executed or explicitly excluded -- no silent
skips.

CI-enforceable: yes -- part of the root ``pytest tests/`` invocation, and the
fixture is structurally validated by ``scripts/gen_parity_fixtures.py`` under
``make parity-fixtures-check``.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Locate source files
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VAULT_RESOLVER_TS = _REPO_ROOT / "visualizer" / "lib" / "vaultResolver.ts"
_VECTORS_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "parity" / "vault-resolution.json"
)

# Make vault_path importable
_SCRIPTS_DIR = str(_REPO_ROOT / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import vault_path  # noqa: E402


# ===========================================================================
# Layer 1: VAULT_FORBIDDEN_PREFIXES list parity (source-text parse)
# ===========================================================================


def _parse_ts_prefixes(ts_source: str) -> list[str]:
    """Extract string arguments from the ``VAULT_FORBIDDEN_PREFIXES`` array.

    Returns the raw string literals (``.claude``, ``Library``, ``/System``,
    ...) so they can be compared structurally against the Python source
    literals. Both sides expand ``~`` / ``_home`` at runtime; we compare the
    raw template segments here.
    """
    m = re.search(
        r"const\s+VAULT_FORBIDDEN_PREFIXES\s*:\s*[^=]+=\s*\[(.*?)\]",
        ts_source,
        re.DOTALL,
    )
    assert m, "Could not find VAULT_FORBIDDEN_PREFIXES array in vaultResolver.ts"
    return re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))


def _normalize_py_prefixes(py_prefixes: tuple[str, ...]) -> list[str]:
    """Reduce Python prefixes to home-relative or absolute segments."""
    home = str(Path.home())
    out: list[str] = []
    for p in py_prefixes:
        if p.startswith(home + "/"):
            out.append(p[len(home) + 1 :])
        else:
            out.append(p)
    return sorted(out)


def _normalize_ts_prefixes(raw: list[str]) -> list[str]:
    """Drop the ``_home`` variable name; keep path segments."""
    return sorted(s for s in raw if s != "_home")


class TestVaultForbiddenPrefixListParity:
    """The forbidden-prefix *list* must stay byte-identical across languages."""

    def test_ts_file_exists(self) -> None:
        assert _VAULT_RESOLVER_TS.exists(), (
            f"vaultResolver.ts not found at {_VAULT_RESOLVER_TS}"
        )

    def test_python_prefixes_present(self) -> None:
        assert vault_path._VAULT_FORBIDDEN_PREFIXES, (
            "_VAULT_FORBIDDEN_PREFIXES is empty in vault_path.py"
        )

    def test_forbidden_prefix_lists_in_sync(self) -> None:
        raw_ts = _parse_ts_prefixes(_VAULT_RESOLVER_TS.read_text(encoding="utf-8"))
        assert _normalize_ts_prefixes(raw_ts) == _normalize_py_prefixes(
            vault_path._VAULT_FORBIDDEN_PREFIXES
        ), (
            "ARC-004: VAULT_FORBIDDEN_PREFIXES mismatch between Python and TypeScript.\n"
            "Update both files to keep them in sync."
        )

    def test_claude_config_dir_is_forbidden(self) -> None:
        raw_ts = _parse_ts_prefixes(_VAULT_RESOLVER_TS.read_text(encoding="utf-8"))
        assert str(Path.home() / ".claude") in vault_path._VAULT_FORBIDDEN_PREFIXES
        assert ".claude" in raw_ts

    def test_system_paths_are_forbidden(self) -> None:
        raw_ts = _parse_ts_prefixes(_VAULT_RESOLVER_TS.read_text(encoding="utf-8"))
        for p in ["/System", "/usr", "/bin", "/sbin", "/etc"]:
            assert p in vault_path._VAULT_FORBIDDEN_PREFIXES
            assert p in raw_ts


# ===========================================================================
# Layer 2: resolution-vector parity (shared fixture)
# ===========================================================================

_FIXTURE_DATA = json.loads(_VECTORS_FIXTURE.read_text(encoding="utf-8"))
_VECTORS: list[dict[str, Any]] = _FIXTURE_DATA["vectors"]
_PYTHON_VECTORS = [
    v for v in _VECTORS if "python" in v.get("applies_to", ["python", "typescript"])
]
_EXCLUDED_FROM_PYTHON = [
    v["name"]
    for v in _VECTORS
    if "python" not in v.get("applies_to", ["python", "typescript"])
]


def _subst(value: Any, tmp: str) -> Any:
    """Replace the ``{TMP}`` token in strings (recursively for lists)."""
    if isinstance(value, str):
        return value.replace("{TMP}", tmp)
    if isinstance(value, list):
        return [_subst(v, tmp) for v in value]
    return value


def _materialize(
    vec: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Build the kwargs for ``resolve_vault(**kwargs)`` from a vector.

    Side-effects: sets env vars, creates dirs, writes the ``.claude/vault``
    marker and ``vaults.yaml`` under the materialized HOME.
    """
    tmp = str(tmp_path)

    home = _subst(vec.get("env_HOME", "{TMP}"), tmp)
    monkeypatch.setenv("HOME", home)

    # Clear every env var a vector can set, then apply the vector's value.
    # This prevents leakage between parametrized cases sharing a process.
    for var in ("CLAUDE_VAULT", "VAULT_ROOT", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    if "env_CLAUDE_VAULT" in vec and vec["env_CLAUDE_VAULT"] is not None:
        monkeypatch.setenv("CLAUDE_VAULT", _subst(vec["env_CLAUDE_VAULT"], tmp))
    if "env_VAULT_ROOT" in vec and vec["env_VAULT_ROOT"] is not None:
        monkeypatch.setenv("VAULT_ROOT", _subst(vec["env_VAULT_ROOT"], tmp))

    for d in vec.get("mkdir", []):
        Path(_subst(d, tmp)).mkdir(parents=True, exist_ok=True)

    cwd = _subst(vec.get("cwd", "{TMP}"), tmp)
    marker = _subst(vec.get("cwd_marker"), tmp)
    if marker:
        marker_path = Path(marker)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            _subst(vec.get("cwd_marker_content", ""), tmp), encoding="utf-8"
        )

    if "vaults_yaml" in vec:
        config_dir = Path(home) / ".config" / "parsidion"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "vaults.yaml").write_text(
            _subst(vec["vaults_yaml"], tmp), encoding="utf-8"
        )

    kwargs: dict[str, Any] = {"cwd": cwd}
    if "explicit" in vec and vec["explicit"] is not None:
        kwargs["explicit"] = _subst(vec["explicit"], tmp)
    return kwargs


@pytest.fixture(autouse=True)
def _clear_resolve_cache() -> Generator[None]:
    """resolve_vault is lru_cached on (explicit, cwd); clear between cases."""
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    yield
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]


class TestVaultResolutionVectors:
    """Fixture-driven parity: every Python-applicable vector must hold."""

    def test_fixture_version_matches_module(self) -> None:
        from scripts.gen_parity_fixtures import VECTORS_VERSION

        assert _FIXTURE_DATA["version"] == VECTORS_VERSION, (
            "vault-resolution.json version drifted from gen_parity_fixtures.py; "
            "regenerate and update both consumers."
        )

    def test_no_vector_silently_skipped_on_python(self) -> None:
        """Every vector must either run on Python or be explicitly excluded.

        A vector with no applies_to runs on both sides. A vector scoped to
        typescript-only must be recorded here so the exclusion is auditable,
        not accidental.
        """
        ran = {v["name"] for v in _PYTHON_VECTORS}
        excluded = set(_EXCLUDED_FROM_PYTHON)
        assert ran | excluded == {v["name"] for v in _VECTORS}, (
            "vector accounting mismatch -- a vector is neither run nor excluded"
        )
        # The typescript-only exclusions are a documented design split, not
        # unimplemented behaviour. Pin the set so a new ts-only vector forces
        # a deliberate ack here.
        assert set(_EXCLUDED_FROM_PYTHON) == {
            "vault-root-overrides-default-typescript",
            "vault-root-forbidden-rejected-typescript",
        }, (
            "TypeScript-only vector set changed; update this assertion to "
            "acknowledge the new exclusion (or make the vector shared)."
        )

    @pytest.mark.parametrize("vec", _PYTHON_VECTORS, ids=lambda v: v["name"])
    def test_resolve_vault_matches_fixture(
        self, vec: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kwargs = _materialize(vec, tmp_path, monkeypatch)
        if "expect_error" in vec:
            with pytest.raises(vault_path.VaultConfigError):
                vault_path.resolve_vault(**kwargs)
        else:
            result = vault_path.resolve_vault(**kwargs)
            expected = Path(_subst(vec["expect"], str(tmp_path)))
            # Resolve both sides so macOS /private prefixing and symlinks
            # (e.g. /etc -> /private/etc) don't make equal paths compare unequal.
            assert result.resolve() == expected.resolve(), (
                f"{vec['name']}: expected {expected.resolve()}, got {result.resolve()}"
            )
