"""Content-drift tests for par-mem documentation surfaces (Tasks 7, 9, 10)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVaultExplorerBridge:
    def test_agent_has_code_memory_bridge_section(self) -> None:
        agent = (REPO_ROOT / "agents" / "vault-explorer.md").read_text(encoding="utf-8")
        assert "## Code-Memory Bridge (par-mem)" in agent
        assert "par-mem find-code" in agent
        assert "par-mem find-symbol" in agent
        assert "--json" in agent
        # Availability probe + graceful-absence rules must be spelled out.
        assert "/health" in agent
        assert "Never treat par-mem absence" in agent
        # Hits merge into the standard sections.
        assert "## Answer" in agent and "## Sources" in agent
