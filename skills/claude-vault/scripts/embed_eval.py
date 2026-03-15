#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""Embedding evaluation harness for Claude Vault.

Compares embedding models × chunking strategies using Claude-generated
ground-truth queries. Reports Recall@1/5/10 and MRR in a Rich table.

Usage:
    # Full pipeline (generate queries then evaluate):
    uv run embed_eval.py

    # Generate ground truth only (100 notes, 3 queries each):
    uv run embed_eval.py --generate --notes 100 --queries-per-note 3

    # Evaluate with cached queries (default models + chunking):
    uv run embed_eval.py --eval

    # Custom models and chunking:
    uv run embed_eval.py --models "BAAI/bge-small-en-v1.5,BAAI/bge-base-en-v1.5" \\
                         --chunking "whole,paragraph"

    # Limit scope for quick test:
    uv run embed_eval.py --notes 20 --queries-per-note 2 --top-k 5
"""

import argparse
import json
import os
import random
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from fastembed import TextEmbedding  # type: ignore[import-untyped]
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

sys.path.insert(0, str(Path(__file__).parent))
import vault_common  # noqa: E402

console = Console()

_DEFAULT_MODELS: list[str] = [
    "BAAI/bge-small-en-v1.5",
    "BAAI/bge-base-en-v1.5",
    "nomic-ai/nomic-embed-text-v1.5",
]
_DEFAULT_CHUNKING: list[str] = ["whole", "paragraph", "sliding_512_128"]
_DEFAULT_QUERIES_FILE: Path = vault_common.VAULT_ROOT / "embed_eval_queries.yaml"
_DEFAULT_NOTES_SAMPLE: int = 100
_DEFAULT_QUERIES_PER_NOTE: int = 3
_DEFAULT_TOP_K: int = 10
_CLAUDE_TIMEOUT: int = 30  # seconds per claude -p call
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
    """Evaluation results for one model × chunking combination."""
    model: str
    chunking: str
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_k: float = 0.0
    mrr: float = 0.0
    total_queries: int = 0
    top_k: int = 10
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Chunking helpers
# ---------------------------------------------------------------------------


def _note_title(note_path: Path, content: str) -> str:
    """Extract note title from first # heading, falling back to stem."""
    body = vault_common.get_body(content)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip()
    return note_path.stem.replace("-", " ").title()


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
        import re as _re
        paragraphs = [p.strip() for p in _re.split(r"\n{2,}", body) if p.strip()]
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
            chunk_text = full_text[start:end]
            chunks.append((stem, chunk_text))
            if end >= len(full_text):
                break
            start += chunk_size - overlap
        return chunks

    # Fallback: whole
    text = f"{title}\n{tags_str}\n{body}"
    return [(stem, text[:_MAX_TEXT_CHARS])]


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


def _open_mem_db() -> sqlite3.Connection:
    """Open an in-memory SQLite database with sqlite-vec loaded."""
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        """
        CREATE TABLE chunks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            stem      TEXT NOT NULL,
            embedding BLOB NOT NULL
        )
        """
    )
    return conn


def _pack_vec(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def build_index(
    notes: list[Path],
    model_name: str,
    chunking: str,
    progress: Progress | None = None,
    task_id: Any = None,
) -> tuple[sqlite3.Connection, int]:
    """Build an in-memory sqlite-vec index for the given model + chunking combo.

    Returns:
        (conn, chunk_count) — the populated connection and total chunk count.
    """
    all_chunks: list[tuple[str, str]] = []
    for note_path in notes:
        all_chunks.extend(chunk_note(note_path, chunking))

    if not all_chunks:
        return _open_mem_db(), 0

    texts = [t for _, t in all_chunks]
    stems = [s for s, _ in all_chunks]

    model = TextEmbedding(model_name=model_name)
    vectors = list(model.embed(texts))

    conn = _open_mem_db()
    with conn:
        conn.executemany(
            "INSERT INTO chunks (stem, embedding) VALUES (?, ?)",
            [(stem, _pack_vec(list(vec))) for stem, vec in zip(stems, vectors)],
        )

    if progress is not None and task_id is not None:
        progress.advance(task_id)

    return conn, len(all_chunks)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def retrieve_stems(
    query_text: str,
    conn: sqlite3.Connection,
    model: TextEmbedding,
    top_k: int,
) -> list[str]:
    """Embed *query_text* and return top-K unique note stems by cosine similarity.

    For chunked indexes, deduplicates by stem preserving first-occurrence rank.
    """
    query_vec = list(model.embed([query_text]))[0]
    query_blob = _pack_vec(list(query_vec))

    cursor = conn.execute(
        """
        SELECT stem,
               (1.0 - vec_distance_cosine(embedding, ?)) AS score
        FROM chunks
        ORDER BY score DESC
        LIMIT ?
        """,
        (query_blob, top_k * 5),  # fetch extra to account for deduplication
    )
    seen: set[str] = set()
    result: list[str] = []
    for stem, _ in cursor.fetchall():
        if stem not in seen:
            seen.add(stem)
            result.append(stem)
            if len(result) >= top_k:
                break
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def compute_metrics(
    eval_items: list[EvalItem],
    conn: sqlite3.Connection,
    model_name: str,
    top_k: int,
) -> tuple[float, float, float, float, int]:
    """Compute Recall@1, @5, @top_k, and MRR for all eval items.

    Returns:
        (recall_1, recall_5, recall_k, mrr, total_queries)
    """
    model = TextEmbedding(model_name=model_name)
    hits_1 = hits_5 = hits_k = 0
    rr_sum = 0.0
    total = 0

    for item in eval_items:
        for query in item.queries:
            total += 1
            stems = retrieve_stems(query, conn, model, top_k)
            rank = None
            for i, s in enumerate(stems, 1):
                if s == item.stem:
                    rank = i
                    break
            if rank is not None:
                if rank == 1:
                    hits_1 += 1
                if rank <= 5:
                    hits_5 += 1
                hits_k += 1
                rr_sum += 1.0 / rank

    if total == 0:
        return 0.0, 0.0, 0.0, 0.0, 0

    return (
        hits_1 / total,
        hits_5 / total,
        hits_k / total,
        rr_sum / total,
        total,
    )


# ---------------------------------------------------------------------------
# Ground truth generation
# ---------------------------------------------------------------------------


def _call_claude(prompt: str, timeout: int = _CLAUDE_TIMEOUT) -> str | None:
    """Call `claude -p` with CLAUDECODE unset. Returns stdout or None on failure."""
    env = vault_common.env_without_claudecode()
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


def generate_queries_for_note(
    note_path: Path,
    queries_per_note: int,
) -> list[str]:
    """Ask Claude to generate *queries_per_note* search queries for the note.

    Returns a list of query strings (may be shorter than requested on failure).
    """
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    body = vault_common.get_body(content).strip()
    fm = vault_common.parse_frontmatter(content)
    title = _note_title(note_path, content)
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags_str = ", ".join(str(t) for t in tags) if tags else "none"

    # Truncate body for the prompt
    body_snippet = body[:800]

    prompt = (
        f"You are generating evaluation queries for a semantic search benchmark.\n\n"
        f"Below is a vault note. Generate exactly {queries_per_note} distinct search "
        f"queries that a developer would type to find this specific note.\n\n"
        f"Rules:\n"
        f"- Vary specificity: include at least one broad and one specific query\n"
        f"- Use natural language (not keywords only)\n"
        f"- Do NOT include the exact note title as a query\n"
        f"- Return ONLY a JSON object: {{\"queries\": [\"q1\", \"q2\", ...]}}\n\n"
        f"Note title: {title}\n"
        f"Tags: {tags_str}\n"
        f"Content snippet:\n{body_snippet}\n"
    )

    raw = _call_claude(prompt)
    if not raw:
        return []

    # Extract JSON from the response (Claude may wrap it in markdown)
    import re as _re
    json_match = _re.search(r'\{[^{}]*"queries"[^{}]*\}', raw, _re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            queries = data.get("queries", [])
            if isinstance(queries, list):
                return [str(q) for q in queries[:queries_per_note] if q]
        except json.JSONDecodeError:
            pass
    return []


def generate_ground_truth(
    notes_sample: int,
    queries_per_note: int,
    output_file: Path,
    seed: int = 42,
) -> list[EvalItem]:
    """Sample notes, generate queries via Claude, save to YAML, return items."""
    all_notes = vault_common.all_vault_notes()
    # Filter out daily notes
    non_daily = [n for n in all_notes if "Daily" not in n.parts]

    rng = random.Random(seed)
    sample = rng.sample(non_daily, min(notes_sample, len(non_daily)))

    items: list[EvalItem] = []
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating queries", total=len(sample))
        for note_path in sample:
            queries = generate_queries_for_note(note_path, queries_per_note)
            if queries:
                items.append(EvalItem(
                    stem=note_path.stem,
                    path=str(note_path),
                    queries=queries,
                ))
            else:
                failed += 1
            progress.advance(task)

    if failed:
        console.print(f"[yellow]Warning: {failed} notes failed query generation[/yellow]")

    # Save to YAML
    output_file.parent.mkdir(parents=True, exist_ok=True)
    data = [{"stem": i.stem, "path": i.path, "queries": i.queries} for i in items]
    output_file.write_text(yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
    console.print(f"[green]Saved {len(items)} eval items → {output_file}[/green]")
    return items


def load_ground_truth(queries_file: Path) -> list[EvalItem]:
    """Load ground-truth items from a YAML file."""
    raw = yaml.safe_load(queries_file.read_text(encoding="utf-8"))
    items: list[EvalItem] = []
    for entry in raw:
        items.append(EvalItem(
            stem=entry["stem"],
            path=entry["path"],
            queries=entry["queries"],
        ))
    return items


# ---------------------------------------------------------------------------
# Evaluation runner
# ---------------------------------------------------------------------------


def run_evaluation(
    eval_items: list[EvalItem],
    models: list[str],
    chunking_strategies: list[str],
    top_k: int,
) -> list[ComboResult]:
    """Run the full model × chunking evaluation matrix.

    Loads only notes that appear in eval_items (avoids embedding the whole vault).
    """
    # Resolve note paths from eval items
    note_paths: list[Path] = []
    for item in eval_items:
        p = Path(item.path)
        if p.exists():
            note_paths.append(p)

    if not note_paths:
        console.print("[red]No valid note paths found in eval items.[/red]")
        return []

    results: list[ComboResult] = []
    combos = [(m, c) for m in models for c in chunking_strategies]

    console.print(f"\n[bold]Running {len(combos)} combinations[/bold] "
                  f"({len(eval_items)} notes × {sum(len(i.queries) for i in eval_items)} queries)\n")

    for model_name, chunking in combos:
        label = f"{model_name.split('/')[-1]} / {chunking}"
        console.print(f"  [cyan]Building index:[/cyan] {label}")

        t0 = time.time()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task_id = progress.add_task(f"Embedding ({chunking})", total=None)
            try:
                conn, chunk_count = build_index(note_paths, model_name, chunking)
            except Exception as exc:
                console.print(f"  [red]  Failed to build index: {exc}[/red]")
                continue

        console.print(f"  [dim]  {chunk_count} chunks indexed[/dim]")

        r1, r5, rk, mrr, total = compute_metrics(eval_items, conn, model_name, top_k)
        conn.close()
        elapsed = time.time() - t0

        results.append(ComboResult(
            model=model_name,
            chunking=chunking,
            recall_at_1=r1,
            recall_at_5=r5,
            recall_at_k=rk,
            mrr=mrr,
            total_queries=total,
            top_k=top_k,
            elapsed_s=elapsed,
        ))

    return results


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def display_results(results: list[ComboResult], top_k: int) -> None:
    """Render a Rich table comparing all model × chunking combinations."""
    if not results:
        console.print("[yellow]No results to display.[/yellow]")
        return

    table = Table(
        title=f"Embedding Evaluation — Recall@K and MRR (top_k={top_k})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="bold", no_wrap=True)
    table.add_column("Chunking", no_wrap=True)
    table.add_column("R@1", justify="right")
    table.add_column("R@5", justify="right")
    table.add_column(f"R@{top_k}", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Queries", justify="right", style="dim")
    table.add_column("Time(s)", justify="right", style="dim")

    # Sort by MRR descending for easy comparison
    sorted_results = sorted(results, key=lambda r: r.mrr, reverse=True)
    best_mrr = sorted_results[0].mrr if sorted_results else 0.0

    for res in sorted_results:
        model_short = res.model.split("/")[-1]
        mrr_str = f"{res.mrr:.3f}"
        if res.mrr == best_mrr:
            mrr_str = f"[bold green]{mrr_str}[/bold green]"

        def fmt(v: float) -> str:
            return f"{v:.3f}"

        table.add_row(
            model_short,
            res.chunking,
            fmt(res.recall_at_1),
            fmt(res.recall_at_5),
            fmt(res.recall_at_k),
            mrr_str,
            str(res.total_queries),
            f"{res.elapsed_s:.1f}",
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the eval pipeline."""
    parser = argparse.ArgumentParser(
        description="Embedding evaluation harness for Claude Vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        default=False,
        help="Generate ground-truth queries via Claude (even if queries file exists).",
    )
    parser.add_argument(
        "--eval",
        action="store_true",
        default=False,
        help="Run evaluation only (skip query generation, requires existing queries file).",
    )
    parser.add_argument(
        "--notes",
        type=int,
        default=_DEFAULT_NOTES_SAMPLE,
        metavar="N",
        help=f"Number of notes to sample for ground truth (default: {_DEFAULT_NOTES_SAMPLE}).",
    )
    parser.add_argument(
        "--queries-per-note",
        type=int,
        default=_DEFAULT_QUERIES_PER_NOTE,
        metavar="K",
        help=f"Queries to generate per note (default: {_DEFAULT_QUERIES_PER_NOTE}).",
    )
    parser.add_argument(
        "--queries-file",
        type=Path,
        default=_DEFAULT_QUERIES_FILE,
        metavar="FILE",
        help=f"YAML ground-truth file (default: {_DEFAULT_QUERIES_FILE}).",
    )
    parser.add_argument(
        "--models",
        default=",".join(_DEFAULT_MODELS),
        metavar="M1,M2",
        help="Comma-separated fastembed model IDs.",
    )
    parser.add_argument(
        "--chunking",
        default=",".join(_DEFAULT_CHUNKING),
        metavar="C1,C2",
        help="Comma-separated chunking strategies: whole, paragraph, sliding_SIZE_OVERLAP.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=_DEFAULT_TOP_K,
        metavar="K",
        help=f"Evaluate Recall@K (default: {_DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for note sampling (default: 42).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Save results as JSON to FILE.",
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    chunking_strategies = [c.strip() for c in args.chunking.split(",") if c.strip()]
    queries_file: Path = args.queries_file

    # Determine pipeline
    need_generate = args.generate or (not args.eval and not queries_file.exists())
    need_eval = args.eval or not args.generate

    # Phase 1: Generate
    if need_generate:
        console.print(f"\n[bold]Phase 1: Generating ground-truth queries[/bold]")
        console.print(
            f"  Sampling [cyan]{args.notes}[/cyan] notes, "
            f"[cyan]{args.queries_per_note}[/cyan] queries each via Claude\n"
        )
        eval_items = generate_ground_truth(
            notes_sample=args.notes,
            queries_per_note=args.queries_per_note,
            output_file=queries_file,
            seed=args.seed,
        )
    else:
        if not queries_file.exists():
            console.print(
                f"[red]Queries file not found: {queries_file}[/red]\n"
                "Run without --eval to generate queries first."
            )
            sys.exit(1)
        eval_items = load_ground_truth(queries_file)
        console.print(
            f"\n[dim]Loaded {len(eval_items)} eval items from {queries_file}[/dim]"
        )

    if not eval_items:
        console.print("[red]No eval items — cannot run evaluation.[/red]")
        sys.exit(1)

    # Phase 2: Evaluate
    if need_eval:
        console.print(f"\n[bold]Phase 2: Evaluation matrix[/bold]")
        console.print(f"  Models:   {models}")
        console.print(f"  Chunking: {chunking_strategies}")
        console.print(f"  top_k:    {args.top_k}\n")

        results = run_evaluation(
            eval_items=eval_items,
            models=models,
            chunking_strategies=chunking_strategies,
            top_k=args.top_k,
        )

        display_results(results, args.top_k)

        if args.output:
            out_data = [
                {
                    "model": r.model,
                    "chunking": r.chunking,
                    "recall_at_1": r.recall_at_1,
                    "recall_at_5": r.recall_at_5,
                    f"recall_at_{r.top_k}": r.recall_at_k,
                    "mrr": r.mrr,
                    "total_queries": r.total_queries,
                    "elapsed_s": r.elapsed_s,
                }
                for r in results
            ]
            args.output.write_text(json.dumps(out_data, indent=2), encoding="utf-8")
            console.print(f"[green]Results saved → {args.output}[/green]")


if __name__ == "__main__":
    main()
