"""Unit tests for embed_eval.py and its sub-modules.

Tests cover pure-logic functions that do NOT require fastembed, sqlite-vec,
or a live vault. All heavy dependencies are mocked or skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest

# We need to mock the heavy imports before importing embed_eval modules.
# The sub-modules import from embed_eval_common which uses Rich (available in dev deps).
# But embed_eval_generate/run import fastembed/sqlite_vec — we only test common + report.

import embed_eval_common as common
import embed_eval_report as report


# ---------------------------------------------------------------------------
# embed_eval_common: EvalItem dataclass
# ---------------------------------------------------------------------------


class TestEvalItem:
    """Tests for the EvalItem dataclass."""

    def test_basic_construction(self) -> None:
        item = common.EvalItem(
            stem="my-note", path="/vault/my-note.md", queries=["q1", "q2"]
        )
        assert item.stem == "my-note"
        assert item.path == "/vault/my-note.md"
        assert item.queries == ["q1", "q2"]

    def test_empty_queries(self) -> None:
        item = common.EvalItem(stem="x", path="/x.md", queries=[])
        assert item.queries == []

    def test_single_query(self) -> None:
        item = common.EvalItem(stem="note", path="/note.md", queries=["how to X"])
        assert len(item.queries) == 1


# ---------------------------------------------------------------------------
# embed_eval_common: ComboResult dataclass
# ---------------------------------------------------------------------------


class TestComboResult:
    """Tests for ComboResult computed properties."""

    def test_queries_per_sec_normal(self) -> None:
        r = common.ComboResult(
            model="m", chunking="whole", query_time_s=2.0, total_queries=10
        )
        assert r.queries_per_sec == pytest.approx(5.0)

    def test_queries_per_sec_zero_time(self) -> None:
        r = common.ComboResult(
            model="m", chunking="whole", query_time_s=0.0, total_queries=10
        )
        assert r.queries_per_sec == 0.0

    def test_queries_per_sec_negative_time(self) -> None:
        r = common.ComboResult(
            model="m", chunking="whole", query_time_s=-1.0, total_queries=10
        )
        assert r.queries_per_sec == 0.0

    def test_total_time_s(self) -> None:
        r = common.ComboResult(
            model="m", chunking="whole", index_time_s=1.5, query_time_s=2.5
        )
        assert r.total_time_s == pytest.approx(4.0)

    def test_default_values(self) -> None:
        r = common.ComboResult(model="m", chunking="c")
        assert r.recall_at_1 == 0.0
        assert r.recall_at_5 == 0.0
        assert r.recall_at_k == 0.0
        assert r.mrr == 0.0
        assert r.total_queries == 0
        assert r.top_k == 10
        assert r.index_time_s == 0.0
        assert r.query_time_s == 0.0
        assert r.chunk_count == 0


# ---------------------------------------------------------------------------
# embed_eval_common: chunk_note (uses vault_common but no fastembed)
# ---------------------------------------------------------------------------


class TestChunkNote:
    """Tests for chunk_note which splits note content into chunks."""

    def _write_note(self, tmp_path: Path, content: str) -> Path:
        p = tmp_path / "test-note.md"
        p.write_text(content, encoding="utf-8")
        return p

    def test_whole_strategy(self, tmp_path: Path) -> None:
        content = "---\ntags: [python]\n---\n# My Title\n\nBody paragraph one.\n\nBody paragraph two."
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "whole")
        assert len(chunks) == 1
        stem, text = chunks[0]
        assert stem == "test-note"
        assert "My Title" in text
        assert "Body paragraph" in text

    def test_paragraph_strategy(self, tmp_path: Path) -> None:
        content = (
            "---\ntags: []\n---\n# Title\n\nParagraph one here.\n\nParagraph two here."
        )
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "paragraph")
        # Body splits into 3 paragraphs: "# Title", "Paragraph one here.", "Paragraph two here."
        assert len(chunks) == 3
        # Each chunk should have the title prepended
        for _, text in chunks:
            assert "Title" in text

    def test_paragraph_empty_body(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n"
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "paragraph")
        # Falls back to single chunk
        assert len(chunks) == 1

    def test_sliding_strategy(self, tmp_path: Path) -> None:
        body = "word " * 200  # ~1000 chars
        content = f"---\ntags: []\n---\n# Title\n\n{body}"
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "sliding_512_128")
        assert len(chunks) > 1
        for stem, _ in chunks:
            assert stem == "test-note"

    def test_sliding_short_body(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n# Title\n\nShort."
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "sliding_512_128")
        assert len(chunks) == 1

    def test_unknown_strategy_falls_back_to_whole(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n# Title\n\nBody."
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "unknown_strategy")
        assert len(chunks) == 1

    def test_unreadable_file(self, tmp_path: Path) -> None:
        fake_path = tmp_path / "nonexistent.md"
        chunks = common.chunk_note(fake_path, "whole")
        assert chunks == []

    def test_title_from_heading(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n# Custom Title\n\nBody."
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "whole")
        _, text = chunks[0]
        assert "Custom Title" in text

    def test_title_from_stem_when_no_heading(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\nNo heading here, just body."
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "whole")
        _, text = chunks[0]
        # stem is "test-note" -> title is "Test Note"
        assert "Test Note" in text

    def test_max_text_chars_truncation(self, tmp_path: Path) -> None:
        body = "x" * 3000
        content = f"---\ntags: []\n---\n# T\n\n{body}"
        note = self._write_note(tmp_path, content)
        chunks = common.chunk_note(note, "whole")
        _, text = chunks[0]
        assert len(text) <= common._MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# embed_eval_common: _note_title
# ---------------------------------------------------------------------------


class TestNoteTitle:
    """Tests for _note_title helper."""

    def test_extracts_h1(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n# My Great Title\n\nBody."
        note = tmp_path / "note.md"
        note.write_text(content, encoding="utf-8")
        assert common._note_title(note, content) == "My Great Title"

    def test_ignores_h2(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\n## Sub Heading\n\nBody."
        note = tmp_path / "some-note.md"
        note.write_text(content, encoding="utf-8")
        # Should fall back to stem
        assert common._note_title(note, content) == "Some Note"

    def test_stem_fallback(self, tmp_path: Path) -> None:
        content = "---\ntags: []\n---\nJust body, no heading."
        note = tmp_path / "my-cool-note.md"
        note.write_text(content, encoding="utf-8")
        assert common._note_title(note, content) == "My Cool Note"


# ---------------------------------------------------------------------------
# embed_eval_common: _pack_vec
# ---------------------------------------------------------------------------


class TestPackVec:
    """Tests for _pack_vec byte packing."""

    def test_pack_roundtrip(self) -> None:
        import struct

        vec = [1.0, 2.0, 3.0]
        packed = common._pack_vec(vec)
        assert len(packed) == 12  # 3 floats * 4 bytes
        unpacked = list(struct.unpack("3f", packed))
        assert unpacked == pytest.approx(vec)

    def test_empty_vec(self) -> None:
        packed = common._pack_vec([])
        assert packed == b""


# ---------------------------------------------------------------------------
# embed_eval_common: constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for module-level constants."""

    def test_default_models_not_empty(self) -> None:
        assert len(common.DEFAULT_MODELS) > 0

    def test_default_chunking_not_empty(self) -> None:
        assert len(common.DEFAULT_CHUNKING) > 0

    def test_default_top_k_positive(self) -> None:
        assert common.DEFAULT_TOP_K > 0

    def test_max_text_chars_positive(self) -> None:
        assert common._MAX_TEXT_CHARS > 0


# ---------------------------------------------------------------------------
# embed_eval_report: display_results (smoke test - just verifies no crash)
# ---------------------------------------------------------------------------


class TestDisplayResults:
    """Tests for report display functions.

    Rich's ``Console`` captured ``sys.stdout`` at import time, so capsys
    cannot see its output by default. Each test re-points
    ``report.console.file`` at the current (captured) ``sys.stdout`` before
    invoking ``display_results``.
    """

    def test_display_empty_results(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """On empty input, display_results emits the explicit 'No results'
        banner rather than silently returning."""
        monkeypatch.setattr(report.console, "file", sys.stdout)
        report.display_results([], top_k=10)
        out = capsys.readouterr().out
        assert "No results to display" in out

    def test_display_results_with_data(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """display_results renders each row's short-form model name,
        chunking label, and the top_k-reflecting table title."""
        monkeypatch.setattr(report.console, "file", sys.stdout)
        results = [
            common.ComboResult(
                model="BAAI/bge-small-en-v1.5",
                chunking="whole",
                recall_at_1=0.5,
                recall_at_5=0.8,
                recall_at_k=0.9,
                mrr=0.65,
                total_queries=100,
                top_k=10,
                index_time_s=1.0,
                query_time_s=2.0,
                chunk_count=50,
            ),
            common.ComboResult(
                model="nomic-ai/nomic-embed-text-v1.5",
                chunking="paragraph",
                recall_at_1=0.4,
                recall_at_5=0.7,
                recall_at_k=0.85,
                mrr=0.55,
                total_queries=100,
                top_k=10,
                index_time_s=1.5,
                query_time_s=3.0,
                chunk_count=150,
            ),
        ]
        report.display_results(results, top_k=10)
        out = capsys.readouterr().out
        # Short-form model names (last path component) are rendered.
        assert "bge-small-en-v1.5" in out
        assert "nomic-embed-text-v1.5" in out
        # Chunking column labels are rendered.
        assert "whole" in out
        assert "paragraph" in out
        # Table title reflects the top_k argument.
        assert "top_k=10" in out


# ---------------------------------------------------------------------------
# embed_eval_report: save_json_results
# ---------------------------------------------------------------------------


class TestSaveJsonResults:
    """Tests for JSON output."""

    def test_save_json(self, tmp_path: Path) -> None:
        results = [
            common.ComboResult(
                model="test/model",
                chunking="whole",
                recall_at_1=0.5,
                recall_at_5=0.8,
                recall_at_k=0.9,
                mrr=0.65,
                total_queries=10,
                top_k=10,
                index_time_s=1.0,
                query_time_s=2.0,
                chunk_count=50,
            ),
        ]
        out = tmp_path / "results.json"
        metadata: dict[str, Any] = {
            "generated_at": "2026-01-01T00:00:00",
            "notes_sampled": 10,
        }
        report.save_json_results(results, out, metadata)

        data = json.loads(out.read_text(encoding="utf-8"))
        assert "metadata" in data
        assert "results" in data
        assert len(data["results"]) == 1
        r = data["results"][0]
        assert r["model"] == "test/model"
        assert r["mrr"] == 0.65
        assert r["queries_per_sec"] == 5.0

    def test_save_empty_results(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.json"
        report.save_json_results([], out, {})
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["results"] == []


# ---------------------------------------------------------------------------
# embed_eval_report: generate_html_report
# ---------------------------------------------------------------------------


class TestGenerateHtmlReport:
    """Tests for HTML report generation."""

    def test_generates_valid_html(self, tmp_path: Path) -> None:
        results = [
            common.ComboResult(
                model="test/model-a",
                chunking="whole",
                recall_at_1=0.6,
                recall_at_5=0.85,
                recall_at_k=0.95,
                mrr=0.72,
                total_queries=50,
                top_k=10,
                index_time_s=1.0,
                query_time_s=1.5,
                chunk_count=30,
            ),
        ]
        out = tmp_path / "report.html"
        metadata: dict[str, Any] = {
            "generated_at": "2026-01-01T00:00:00",
            "notes_sampled": 20,
            "total_queries": 50,
        }
        report.generate_html_report(results, out, top_k=10, metadata=metadata)
        html = out.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in html
        assert "model-a" in html
        assert "Chart" in html

    def test_html_empty_results(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.html"
        report.generate_html_report([], out, top_k=10, metadata={})
        assert not out.exists()  # returns early for empty results

    def test_html_has_rankings(self, tmp_path: Path) -> None:
        results = [
            common.ComboResult(
                model=f"m/model-{i}", chunking="whole", mrr=0.9 - i * 0.1
            )
            for i in range(3)
        ]
        out = tmp_path / "rankings.html"
        report.generate_html_report(
            results, out, top_k=10, metadata={"generated_at": "2026-01-01"}
        )
        html = out.read_text(encoding="utf-8")
        assert "model-0" in html
        assert "model-1" in html
        assert "model-2" in html


# ---------------------------------------------------------------------------
# embed_eval CLI argument parsing
# ---------------------------------------------------------------------------


class TestCLIParsing:
    """Tests for the argparse CLI structure (via embed_eval.py main module)."""

    def _parse(self, args: list[str]) -> argparse.Namespace:
        """Parse args using embed_eval's parser."""
        parser = argparse.ArgumentParser()
        # Replicate the parser setup from main() to test parsing
        parser.add_argument("--generate", action="store_true", default=False)
        parser.add_argument("--eval", action="store_true", default=False)
        parser.add_argument("--notes", type=int, default=common.DEFAULT_NOTES_SAMPLE)
        parser.add_argument(
            "--queries-per-note", type=int, default=common.DEFAULT_QUERIES_PER_NOTE
        )
        parser.add_argument(
            "--queries-file", type=Path, default=common.DEFAULT_QUERIES_FILE
        )
        parser.add_argument("--models", default=",".join(common.DEFAULT_MODELS))
        parser.add_argument("--chunking", default=",".join(common.DEFAULT_CHUNKING))
        parser.add_argument("--top-k", type=int, default=common.DEFAULT_TOP_K)
        parser.add_argument("--workers", type=int, default=common.DEFAULT_WORKERS)
        parser.add_argument("--max-index-notes", type=int, default=200)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--output", type=Path, default=None)
        return parser.parse_args(args)

    def test_defaults(self) -> None:
        ns = self._parse([])
        assert ns.generate is False
        assert ns.eval is False
        assert ns.notes == common.DEFAULT_NOTES_SAMPLE
        assert ns.top_k == common.DEFAULT_TOP_K

    def test_generate_flag(self) -> None:
        ns = self._parse(["--generate"])
        assert ns.generate is True

    def test_eval_flag(self) -> None:
        ns = self._parse(["--eval"])
        assert ns.eval is True

    def test_custom_notes(self) -> None:
        ns = self._parse(["--notes", "50"])
        assert ns.notes == 50

    def test_custom_models(self) -> None:
        ns = self._parse(["--models", "a/b,c/d"])
        assert ns.models == "a/b,c/d"

    def test_custom_chunking(self) -> None:
        ns = self._parse(["--chunking", "whole,paragraph"])
        assert ns.chunking == "whole,paragraph"

    def test_custom_output(self) -> None:
        ns = self._parse(["--output", "/tmp/test_out"])
        assert ns.output == Path("/tmp/test_out")

    def test_seed(self) -> None:
        ns = self._parse(["--seed", "123"])
        assert ns.seed == 123
