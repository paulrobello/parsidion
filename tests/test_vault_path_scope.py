"""ARC-001: ``active_vault_scope`` semantics.

The scope is the mechanism CLI entry points rely on after they stop mutating
``vault_common.VAULT_ROOT`` (card 01a07d69d8d3764189f1db1878275d67):
argument-less ``resolve_vault()`` consults the scope, an explicit argument
still wins, exit restores the outer state, and the config cache is flushed
on both boundaries so argument-less ``load_config()`` follows the scope.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
from vault_path import active_vault_scope  # noqa: E402


def test_scope_answers_argument_less_calls(tmp_vault: Path, tmp_path: Path) -> None:
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    # Sanity: with tmp_vault pinned via CLAUDE_VAULT, argument-less
    # resolution lands on tmp_vault before any scope is active.
    assert vault_common.resolve_vault() == tmp_vault
    with active_vault_scope(vault_b):
        assert vault_common.resolve_vault() == vault_b
    # Exit restores the outer resolution.
    assert vault_common.resolve_vault() == tmp_vault


def test_explicit_beats_scope(tmp_vault: Path, tmp_path: Path) -> None:
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    with active_vault_scope(vault_b):
        # tmp_vault is registered as named vault "test" by the fixture, so
        # both spellings are allowlisted explicit requests.
        assert vault_common.resolve_vault("test") == tmp_vault
        assert vault_common.resolve_vault(tmp_vault) == tmp_vault
        # Explicit calls populate the resolver cache, but the scope
        # short-circuit runs before any cache lookup, so the scope still
        # answers argument-less calls.
        assert vault_common.resolve_vault() == vault_b


def test_nested_scope_restores_outer(tmp_vault: Path, tmp_path: Path) -> None:
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    vault_c = tmp_path / "vault_c"
    vault_c.mkdir()
    with active_vault_scope(vault_b):
        assert vault_common.resolve_vault() == vault_b
        with active_vault_scope(vault_c):
            assert vault_common.resolve_vault() == vault_c
        assert vault_common.resolve_vault() == vault_b
    assert vault_common.resolve_vault() == tmp_vault


def test_config_cache_follows_scope(tmp_vault: Path, tmp_path: Path) -> None:
    """Argument-less load_config() follows the scope; cache flushed on both edges."""
    vault_b = tmp_path / "vault_b"
    vault_b.mkdir()
    (vault_b / "config.yaml").write_text(
        "defaults:\n  haiku_model: scoped-model\n", encoding="utf-8"
    )
    # Pre-scope: cache the default (tmp_vault) config under key None.
    assert vault_common.load_config() == {}
    with active_vault_scope(vault_b):
        assert vault_common.load_config()["defaults"]["haiku_model"] == "scoped-model"
    # Post-exit: cache flushed, argument-less load_config() back to tmp_vault.
    assert vault_common.load_config() == {}


def test_scope_value_needs_no_allowlist(tmp_vault: Path, tmp_path: Path) -> None:
    """The scope value is the entry point's already-resolved vault.

    Programmatic scope entry bypasses the SEC-P001 allowlist by design --
    the allowlist governs string references (CLI flags, env, files), not an
    already-resolved Path handed over in-process.
    """
    vault_unregistered = tmp_path / "unregistered"
    vault_unregistered.mkdir()
    with active_vault_scope(vault_unregistered):
        assert vault_common.resolve_vault() == vault_unregistered
    assert vault_common.resolve_vault() == tmp_vault
