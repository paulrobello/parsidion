"""Shared pytest fixtures for the Parsidion test suite.

ARC-009: Centralised vault-isolation fixture.

The ``tmp_vault`` fixture redirects ``resolve_vault()`` to a temporary
directory via the ``CLAUDE_VAULT`` environment variable — the same public
override path used by production callers.  This replaces the earlier pattern
of ``monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)`` which
relied on a ``sys.modules`` inspection branch inside
``_resolve_vault_cached`` (see vault_path.py for why that branch must stay
for runtime callers like ``update_index.py``).

Usage in a test module::

    def test_something(tmp_vault: Path) -> None:
        # tmp_vault is the resolved vault root (a fresh tmp_path)
        ...

Or as autouse in a test class::

    @pytest.fixture(autouse=True)
    def _use_vault(self, tmp_vault: Path) -> None:
        pass  # side-effect: CLAUDE_VAULT is set for all tests in the class
"""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Return a fresh temporary vault root and wire resolve_vault() to it.

    Sets the ``CLAUDE_VAULT`` environment variable to ``tmp_path`` so that
    ``resolve_vault()`` returns ``tmp_path`` for the duration of the test,
    then clears the ``resolve_vault`` and ``load_config`` LRU caches before
    and after the test.

    The fixture does NOT create vault subdirectories — tests that need the
    standard layout should call ``vault_common.ensure_vault_dirs(tmp_vault)``
    or create dirs manually.
    """
    # Clear caches before setting the env var so any residual cached entry
    # from a previous test cannot bleed into this one.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()

    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))

    yield tmp_path

    # Teardown: clear caches so the next test starts clean.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()
