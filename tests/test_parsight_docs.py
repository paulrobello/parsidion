"""Content-drift tests for parsight documentation surfaces (Tasks 7, 9, 10)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestVaultExplorerBridge:
    def test_agent_has_code_memory_bridge_section(self) -> None:
        agent = (REPO_ROOT / "agents" / "vault-explorer.md").read_text(encoding="utf-8")
        assert "## Code-Memory Bridge (parsight)" in agent
        assert "parsight find-code" in agent
        assert "parsight find-symbol" in agent
        assert "--json" in agent
        # Availability probe + graceful-absence rules must be spelled out.
        assert "/health" in agent
        assert "Never treat parsight absence" in agent
        # Config gate: honor parsight.enabled and the PARSIGHT_MCP_URL override.
        assert "parsight.enabled" in agent
        assert "PARSIGHT_MCP_URL" in agent
        # Hits merge into the standard sections.
        assert "## Answer" in agent and "## Sources" in agent


class TestParsightDoc:
    def test_doc_exists_and_covers_config_keys(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        for token in (
            "parsight:",
            "enabled",
            "binary",
            "timeout_s",
            "backend: auto",
            "PARSIGHT_MCP_URL",
        ):
            assert token in doc, f"missing {token!r} in docs/PARSIGHT.md"

    def test_doc_pins_requirements_section(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "## Requirements" in doc
        for subcommand in ("repos", "watch", "unwatch"):
            assert f"`{subcommand}`" in doc, (
                f"missing subcommand {subcommand!r} in docs/PARSIGHT.md"
            )
        assert "spec 15" in doc
        assert "parsight repos --json" in doc

    def test_doc_states_score_semantics(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "min_score" in doc
        assert "embeddings backend only" in doc

    def test_doc_carries_full_degradation_matrix(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        for row in (
            "parsight.enabled: false",
            "binary missing / health probe fails",
            "vault not yet indexed",
            "subprocess timeout / nonzero exit / garbage JSON",
            "parsight index job fails server-side",
            "parsight AND embeddings both unavailable",
        ):
            assert row in doc, f"degradation row missing: {row!r}"

    def test_doc_has_troubleshooting(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "## Troubleshooting" in doc
        assert "parsidion-parsight.log" in doc
        assert "hook_events.log" in doc

    def test_readme_mentions_parsight(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "docs/PARSIGHT.md" in readme

    def test_doc_notes_any_directory_routing(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "any" in doc and "working directory" in doc
        assert "2026-07-12" in doc

    def test_doc_notes_index_lock_queueing(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "queued behind another job's hold on the global index lock" in doc

    def test_doc_notes_idle_embedder_after_restart(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "ready: false, status: idle" in doc


class TestGraphVizSection:
    def test_doc_documents_parsight_ui(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "## Graph & Visualization" in doc
        assert "parsight ui" in doc

    def test_doc_documents_graph_enrichment(self) -> None:
        doc = (REPO_ROOT / "docs" / "PARSIGHT.md").read_text(encoding="utf-8")
        assert "## Graph enrichment" in doc
        assert "graph.json" in doc
        assert "build_graph.py" in doc
        assert "parsight doc-links --json --targets doc --limit 200000" in doc
        assert "--no-parsight" in doc
        assert "parsight_body_links" in doc
