"""ARC-205: installer/core default-vault-path parity gate.

``installer.vault._default_vault_path`` and
``parsidion.core.vault_path.default_vault_root`` are two implementations of
the same rule: default to ``~/ParsidionVault`` unless only the legacy
``~/ClaudeVault`` exists. This suite runs both over the same input matrix and
asserts identical outputs, so a one-sided edit fails CI structurally
(ENH-005/ENH-009 fixture pattern) instead of silently splitting the default
vault path between the installer and the hooks.

The vaults.yaml/env channels (``VAULT_ROOT``, ``CLAUDE_VAULT``, the
vaults.yaml ``default:`` key) live in the resolver layer above
(:func:`parsidion.core.vault_path.resolve_vault` / ``resolve_vault_server``);
the matrix also pins that they do NOT leak into the default-path layer --
both implementations must return the filesystem answer with those channels
populated.

CI-enforceable: yes -- part of the root ``pytest tests/`` invocation.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Generator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = str(_REPO_ROOT / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from installer.vault import _default_vault_path  # noqa: E402
from parsidion.core.vault_path import default_vault_root  # noqa: E402

RESOLVER_ENV_CHANNELS = ("VAULT_ROOT", "CLAUDE_VAULT")


@pytest.fixture(autouse=True)
def synthetic_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Pin ``Path.home`` to a synthetic home for both implementations.

    The installer copy takes no ``home`` argument, so ``Path.home`` itself is
    pinned; the core copy receives the same home explicitly. Resolver env
    channels are cleared so results reflect the filesystem layer under test.
    """
    home = tmp_path / "home"
    home.mkdir()

    def fake_home() -> Path:
        return home

    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: fake_home()))
    for var in RESOLVER_ENV_CHANNELS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    yield home


def _expected_default(home: Path) -> Path:
    """The rule under test, stated once: legacy only when it is the only one."""
    current = home / "ParsidionVault"
    legacy = home / "ClaudeVault"
    if legacy.exists() and not current.exists():
        return legacy
    return current


def _run_both(home: Path) -> tuple[Path, Path]:
    return _default_vault_path(), default_vault_root(home)


# ---------------------------------------------------------------------------
# Filesystem matrix: identical answers, and equal to the stated rule.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setup", "expected_name"),
    [
        pytest.param("parsidion", "ParsidionVault", id="parsidion-exists"),
        pytest.param("legacy", "ClaudeVault", id="legacy-only"),
        pytest.param(None, "ParsidionVault", id="neither-exists"),
        pytest.param("both", "ParsidionVault", id="both-exist"),
        pytest.param("symlink", "ParsidionVault", id="legacy-symlink-to-current"),
    ],
)
def test_filesystem_matrix(
    setup: str | None, expected_name: str, synthetic_home: Path
) -> None:
    home = synthetic_home
    if setup in ("parsidion", "both", "symlink"):
        (home / "ParsidionVault").mkdir(parents=True, exist_ok=True)
    if setup in ("legacy", "both"):
        (home / "ClaudeVault").mkdir(parents=True, exist_ok=True)
    if setup == "symlink":
        # Post-migration state: migrate_default_vault leaves the legacy path
        # as a compatibility symlink pointing at the current vault.
        (home / "ClaudeVault").symlink_to(home / "ParsidionVault")

    installer_result, core_result = _run_both(home)
    assert installer_result == core_result == _expected_default(home)
    assert installer_result.name == expected_name


# ---------------------------------------------------------------------------
# Resolver channels must not leak into the default-path layer.
# ---------------------------------------------------------------------------


def test_vault_root_env_is_ignored(
    synthetic_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """VAULT_ROOT is a server-resolver channel; neither copy reads it here."""
    monkeypatch.setenv("VAULT_ROOT", str(synthetic_home / "Elsewhere"))
    installer_result, core_result = _run_both(synthetic_home)
    assert installer_result == core_result == _expected_default(synthetic_home)


def test_claude_vault_env_is_ignored(
    synthetic_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLAUDE_VAULT is a hook-resolver channel; neither copy reads it here."""
    monkeypatch.setenv("CLAUDE_VAULT", str(synthetic_home / "Elsewhere"))
    installer_result, core_result = _run_both(synthetic_home)
    assert installer_result == core_result == _expected_default(synthetic_home)


def test_vaults_yaml_default_is_ignored(
    synthetic_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A vaults.yaml ``default:`` key does not change the filesystem default."""
    config_dir = synthetic_home / ".config" / "parsidion"
    config_dir.mkdir(parents=True)
    (config_dir / "vaults.yaml").write_text(
        "vaults:\n  side: /tmp/sidevault\ndefault: side\n",
        encoding="utf-8",
    )
    installer_result, core_result = _run_both(synthetic_home)
    assert installer_result == core_result == _expected_default(synthetic_home)
