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


class TestParMemDoc:
    def test_doc_exists_and_covers_config_keys(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        for token in (
            "par_mem:",
            "enabled",
            "binary",
            "timeout_s",
            "backend: auto",
            "PARMEM_MCP_URL",
        ):
            assert token in doc, f"missing {token!r} in docs/PAR-MEM.md"

    def test_doc_pins_requirements_section(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        assert "## Requirements" in doc
        for subcommand in ("repos", "watch", "unwatch"):
            assert f"`{subcommand}`" in doc, (
                f"missing subcommand {subcommand!r} in docs/PAR-MEM.md"
            )
        assert "spec 15" in doc
        assert "par-mem repos --json" in doc

    def test_doc_states_score_semantics(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        assert "min_score" in doc
        assert "embeddings backend only" in doc

    def test_doc_carries_full_degradation_matrix(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        for row in (
            "par_mem.enabled: false",
            "binary missing / health probe fails",
            "vault not yet indexed",
            "subprocess timeout / nonzero exit / garbage JSON",
            "par-mem index job fails server-side",
            "par-mem AND embeddings both unavailable",
        ):
            assert row in doc, f"degradation row missing: {row!r}"

    def test_doc_has_troubleshooting(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        assert "## Troubleshooting" in doc
        assert "parsidion-parmem.log" in doc
        assert "hook_events.log" in doc

    def test_readme_mentions_par_mem(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "docs/PAR-MEM.md" in readme


class TestGraphVizSection:
    def test_doc_documents_par_mem_ui(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        assert "## Graph & Visualization" in doc
        assert "par-mem ui" in doc

    def test_doc_records_deferred_graph_json_decision(self) -> None:
        doc = (REPO_ROOT / "docs" / "PAR-MEM.md").read_text(encoding="utf-8")
        assert "graph.json" in doc
        assert "build_graph.py" in doc
        assert "Deferred" in doc
