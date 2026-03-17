"""Integration smoke test — skipped when vault is absent."""

import pytest
import vault_common

VAULT_PRESENT = vault_common.VAULT_ROOT.exists()


@pytest.mark.skipif(not VAULT_PRESENT, reason="vault not present")
def test_vault_read_real_note() -> None:
    """Read the first available vault note without errors."""
    from parsidion_mcp.tools.notes import vault_read

    notes = list(vault_common.VAULT_ROOT.rglob("*.md"))
    notes = [n for n in notes if ".obsidian" not in n.parts]

    if not notes:
        pytest.skip("no notes in vault")

    rel = notes[0].relative_to(vault_common.VAULT_ROOT)
    result = vault_read(str(rel))
    assert not result.startswith("ERROR:"), f"vault_read failed: {result}"


@pytest.mark.skipif(not VAULT_PRESENT, reason="vault not present")
def test_vault_context_returns_string() -> None:
    """vault_context returns a non-empty string."""
    from parsidion_mcp.tools.context import vault_context

    result = vault_context(recent_days=30)
    assert isinstance(result, str)
    assert len(result) > 0
