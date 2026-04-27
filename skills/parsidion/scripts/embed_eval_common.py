#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""Shared types, constants, and pure utilities for embed_eval.

This module contains the dataclasses, constants, and helper functions
used by the generate, run, and report phases of the embedding evaluation
harness.
"""

import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console  # type: ignore[import-untyped]

# Ensure sibling scripts are importable (e.g. vault_common)
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import vault_common  # noqa: E402

console = Console()

DEFAULT_MODELS: list[str] = [
    "BAAI/bge-small-en-v1.5",
    "BAAI/bge-base-en-v1.5",
    "nomic-ai/nomic-embed-text-v1.5",
]
DEFAULT_CHUNKING: list[str] = ["whole", "paragraph", "sliding_512_128"]
DEFAULT_QUERIES_FILE: Path = vault_common.VAULT_ROOT / "embed_eval_queries.yaml"
DEFAULT_NOTES_SAMPLE: int = 100
DEFAULT_QUERIES_PER_NOTE: int = 3
DEFAULT_TOP_K: int = 10
DEFAULT_WORKERS: int = 1
CLAUDE_TIMEOUT: int = 30  # seconds per claude -p call
_MAX_TEXT_CHARS: int = 1500


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class EvalItem:
    """A single ground-truth evaluation pair."""

    stem: str
    path: str
    queries: list[str]


@dataclass
class ComboResult:
    """Evaluation results for one model x chunking combination."""

    model: str
    chunking: str
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    total_queries: int = 0
    top_k: int = 10
    index_time_s: float = 0.0  # wall-clock time to embed all notes
    query_time_s: float = 0.0  # wall-clock time to run all queries
    chunk_count: int = 0  # total chunks indexed (>1 note for non-whole)

    @property
    def queries_per_sec(self) -> float:
        """Throughput: queries processed per second during retrieval."""
        if self.query_time_s <= 0:
            return 0.0
        return self.total_queries / self.query_time_s

    @property
    def total_time_s(self) -> float:
        return self.index_time_s + self.query_time_s


# ---------------------------------------------------------------------------
# Pure utility functions
# ---------------------------------------------------------------------------


def _note_title(note_path: Path, content: str) -> str:
    """Extract note title from first # heading, falling back to stem."""
    body = vault_common.get_body(content)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return note_path.stem.replace("-", " ").title()


def _pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def chunk_note(note_path: Path, strategy: str) -> list[tuple[str, str]]:
    """Split a note into (stem, text) chunks according to *strategy*.

    Returns:
        List of (stem, chunk_text) tuples. For 'whole', one tuple per note.
        For 'paragraph'/'sliding_*', multiple tuples sharing the same stem.
    """
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    fm = vault_common.parse_frontmatter(content)
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags_str = ", ".join(str(t) for t in tags) if tags else ""
    title = _note_title(note_path, content)
    body = vault_common.get_body(content).strip()
    stem = note_path.stem

    if strategy == "whole":
        text = f"{title}\n{tags_str}\n{body}"
        return [(stem, text[:_MAX_TEXT_CHARS])]

    if strategy == "paragraph":
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", body) if p.strip()]
        if not paragraphs:
            text = f"{title}\n{tags_str}\n{body}"
            return [(stem, text[:_MAX_TEXT_CHARS])]
        chunks: list[tuple[str, str]] = []
        for para in paragraphs:
            chunk_text = f"{title}\n{para}"
            chunks.append((stem, chunk_text[:_MAX_TEXT_CHARS]))
        return chunks

    # sliding_SIZE_OVERLAP  e.g. "sliding_512_128"
    if strategy.startswith("sliding_"):
        parts = strategy.split("_")
        chunk_size = int(parts[1]) if len(parts) > 1 else 512
        overlap = int(parts[2]) if len(parts) > 2 else 128
        full_text = f"{title}\n{tags_str}\n{body}"
        if len(full_text) <= chunk_size:
            return [(stem, full_text[:_MAX_TEXT_CHARS])]
        chunks = []
        start = 0
        while start < len(full_text):
            end = start + chunk_size
            chunks.append((stem, full_text[start:end]))
            if end >= len(full_text):
                break
            start += chunk_size - overlap
        return chunks

    # Fallback: whole
    text = f"{title}\n{tags_str}\n{body}"
    return [(stem, text[:_MAX_TEXT_CHARS])]
