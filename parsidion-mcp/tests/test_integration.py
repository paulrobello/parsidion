"""Integration tests exercising real MCP tools against an isolated vault."""

from __future__ import annotations

from collections.abc import Generator
from datetime import date
from pathlib import Path

import pytest
import vault_common

from parsidion_mcp.tools.context import vault_context
from parsidion_mcp.tools.notes import vault_read

_TEST_VAULT_NAME = "mcp-integration-test"
_NOTE_PATH = "Patterns/mcp-fixture-note.md"
_NOTE_TITLE = "MCP fixture note"


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Register a small real vault through the public resolver configuration."""
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.clear_config_cache()

    cfg_dir = tmp_path / ".config" / "parsidion"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "vaults.yaml").write_text(
        f"vaults:\n  {_TEST_VAULT_NAME}: {tmp_path}\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))

    patterns = tmp_path / "Patterns"
    patterns.mkdir()
    for stem, title, related in (
        ("mcp-fixture-note", _NOTE_TITLE, "mcp-fixture-reference"),
        ("mcp-fixture-reference", "MCP fixture reference", "mcp-fixture-note"),
    ):
        (patterns / f"{stem}.md").write_text(
            "---\n"
            f"date: {date.today().isoformat()}\n"
            "type: pattern\n"
            "tags: [mcp, fixture]\n"
            "confidence: high\n"
            "sources: []\n"
            f'related: ["[[{related}]]"]\n'
            "---\n"
            f"# {title}\n\n"
            "Exercise the real MCP vault tools with isolated fixture notes.\n",
            encoding="utf-8",
        )

    try:
        yield tmp_path
    finally:
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
        vault_common.clear_config_cache()


def test_vault_read_real_note(tmp_vault: Path) -> None:
    """Read a real note from the registered temporary vault without mocks."""
    assert vault_common.resolve_vault() == tmp_vault
    result = vault_read(_NOTE_PATH, vault=_TEST_VAULT_NAME)
    assert result == (tmp_vault / _NOTE_PATH).read_text(encoding="utf-8")


@pytest.mark.parametrize("vault_ref", [_TEST_VAULT_NAME, "path"])
def test_vault_context_returns_string(tmp_vault: Path, vault_ref: str) -> None:
    """Resolve the fixture by registered name or path and include its note."""
    vault = str(tmp_vault) if vault_ref == "path" else vault_ref
    assert vault_common.resolve_vault(explicit=vault) == tmp_vault
    result = vault_context(recent_days=30, vault=vault)
    assert isinstance(result, str)
    assert _NOTE_TITLE in result
    assert "MCP fixture reference" in result
