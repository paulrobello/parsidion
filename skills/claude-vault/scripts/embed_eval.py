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
ground-truth queries. Reports Recall@1/5/10, MRR, and speed in a Rich table,
then saves a standalone HTML report with charts.

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
import concurrent.futures
import datetime
import json
import random
import re
import sqlite3
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]
import yaml  # type: ignore[import-untyped]
from fastembed import TextEmbedding  # type: ignore[import-untyped]
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskID, TextColumn, TimeElapsedColumn
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
_DEFAULT_WORKERS: int = 3
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
    index_time_s: float = 0.0    # wall-clock time to embed all notes
    query_time_s: float = 0.0    # wall-clock time to run all queries
    chunk_count: int = 0          # total chunks indexed (>1 note for non-whole)

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


# ---------------------------------------------------------------------------
# Index building (in-memory sqlite-vec)
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
    model: TextEmbedding,
    chunking: str,
) -> tuple[sqlite3.Connection, int, float]:
    """Build an in-memory sqlite-vec index for the given chunking strategy.

    Args:
        notes: Note paths to embed.
        model: Pre-loaded TextEmbedding model (shared across chunkings).
        chunking: Chunking strategy string.

    Returns:
        (conn, chunk_count, index_time_s)
    """
    t0 = time.time()
    all_chunks: list[tuple[str, str]] = []
    for note_path in notes:
        all_chunks.extend(chunk_note(note_path, chunking))

    if not all_chunks:
        return _open_mem_db(), 0, time.time() - t0

    texts = [t for _, t in all_chunks]
    stems = [s for s, _ in all_chunks]

    vectors = list(model.embed(texts))

    conn = _open_mem_db()
    with conn:
        conn.executemany(
            "INSERT INTO chunks (stem, embedding) VALUES (?, ?)",
            [(stem, _pack_vec(list(vec))) for stem, vec in zip(stems, vectors)],
        )

    return conn, len(all_chunks), time.time() - t0


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def retrieve_stems(
    query_text: str,
    conn: sqlite3.Connection,
    model: TextEmbedding,
    top_k: int,
) -> list[str]:
    """Embed query and return top-K unique note stems by cosine similarity."""
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
        (query_blob, top_k * 5),
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
    model: TextEmbedding,
    top_k: int,
) -> tuple[float, float, float, float, int, float]:
    """Compute Recall@1, @5, @top_k, MRR, total_queries, and query_time_s.

    Returns:
        (recall_1, recall_5, recall_k, mrr, total_queries, query_time_s)
    """
    hits_1 = hits_5 = hits_k = 0
    rr_sum = 0.0
    total = 0
    t0 = time.time()

    for item in eval_items:
        for query in item.queries:
            total += 1
            stems = retrieve_stems(query, conn, model, top_k)
            rank = next((i for i, s in enumerate(stems, 1) if s == item.stem), None)
            if rank is not None:
                if rank == 1:
                    hits_1 += 1
                if rank <= 5:
                    hits_5 += 1
                hits_k += 1
                rr_sum += 1.0 / rank

    query_time_s = time.time() - t0

    if total == 0:
        return 0.0, 0.0, 0.0, 0.0, 0, 0.0

    return (
        hits_1 / total,
        hits_5 / total,
        hits_k / total,
        rr_sum / total,
        total,
        query_time_s,
    )


# ---------------------------------------------------------------------------
# Parallel worker — one thread per model, all chunkings in serial
# ---------------------------------------------------------------------------


def _eval_model_combos(
    model_name: str,
    chunking_strategies: list[str],
    note_paths: list[Path],
    eval_items: list[EvalItem],
    top_k: int,
    progress_queue: Any,  # multiprocessing.Queue or None
) -> list[ComboResult]:
    """Load model once, then evaluate all chunking strategies for it.

    Designed to run in a ThreadPoolExecutor worker — each thread handles
    one model and iterates over all chunkings serially, avoiding redundant
    model loads.

    Args:
        model_name: fastembed model ID.
        chunking_strategies: List of chunking strategy strings.
        note_paths: Note paths to embed.
        eval_items: Ground-truth items.
        top_k: Recall@K cutoff.
        progress_queue: If provided, put (model_name, chunking) tuples when done.

    Returns:
        List of ComboResult (one per chunking strategy).
    """
    try:
        model = TextEmbedding(model_name=model_name)
    except Exception as exc:
        console.log(f"[red]Failed to load model {model_name}: {exc}[/red]")
        return []

    results: list[ComboResult] = []

    for chunking in chunking_strategies:
        try:
            conn, chunk_count, index_time_s = build_index(note_paths, model, chunking)
            r1, r5, rk, mrr, total, query_time_s = compute_metrics(
                eval_items, conn, model, top_k
            )
            conn.close()
        except Exception as exc:
            console.log(f"[red]  {model_name}/{chunking} failed: {exc}[/red]")
            if progress_queue is not None:
                progress_queue.put((model_name, chunking, False))
            continue

        results.append(
            ComboResult(
                model=model_name,
                chunking=chunking,
                recall_at_1=r1,
                recall_at_5=r5,
                recall_at_k=rk,
                mrr=mrr,
                total_queries=total,
                top_k=top_k,
                index_time_s=index_time_s,
                query_time_s=query_time_s,
                chunk_count=chunk_count,
            )
        )

        if progress_queue is not None:
            progress_queue.put((model_name, chunking, True))

    return results


# ---------------------------------------------------------------------------
# Evaluation runner
# ---------------------------------------------------------------------------


def run_evaluation(
    eval_items: list[EvalItem],
    models: list[str],
    chunking_strategies: list[str],
    top_k: int,
    max_workers: int = _DEFAULT_WORKERS,
) -> list[ComboResult]:
    """Run the full model × chunking evaluation matrix in parallel.

    One thread per model; each thread evaluates all chunking strategies
    serially (model loaded once per thread).

    Args:
        eval_items: Ground-truth items.
        models: List of fastembed model IDs.
        chunking_strategies: List of chunking strategy names.
        top_k: Recall@K cutoff.
        max_workers: Maximum parallel threads (one per model).

    Returns:
        List of ComboResult sorted by MRR descending.
    """
    # Index ALL vault notes — the eval must find the correct note among the full
    # corpus, not just among the sampled notes. Searching only the sampled notes
    # trivially yields perfect recall regardless of model quality.
    note_paths: list[Path] = vault_common.all_vault_notes()

    if not note_paths:
        console.print("[red]No vault notes found.[/red]")
        return []

    total_combos = len(models) * len(chunking_strategies)
    total_queries = sum(len(i.queries) for i in eval_items)
    console.print(
        f"\n[bold]Running {total_combos} combinations in parallel[/bold] "
        f"({len(eval_items)} eval notes out of {len(note_paths)} total, "
        f"{total_queries} queries, {len(models)} threads)\n"
    )

    all_results: list[ComboResult] = []
    workers = min(max_workers, len(models))

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Evaluating combos", total=total_combos)

        # Use a simple list to collect completed combo notifications
        # ThreadPoolExecutor: each future handles one model, returns list[ComboResult]
        futures: dict[concurrent.futures.Future[list[ComboResult]], str] = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            for model_name in models:
                future = executor.submit(
                    _eval_model_combos,
                    model_name,
                    chunking_strategies,
                    note_paths,
                    eval_items,
                    top_k,
                    None,  # no queue needed — we poll futures
                )
                futures[future] = model_name

            for future in concurrent.futures.as_completed(futures):
                model_name = futures[future]
                try:
                    combo_results = future.result()
                    all_results.extend(combo_results)
                    progress.advance(task_id, len(combo_results))
                    for r in combo_results:
                        short = r.model.split("/")[-1]
                        console.log(
                            f"  [green]✓[/green] {short}/{r.chunking} "
                            f"MRR={r.mrr:.3f} "
                            f"idx={r.index_time_s:.1f}s "
                            f"qry={r.queries_per_sec:.1f}q/s"
                        )
                except Exception as exc:
                    console.log(f"[red]  Model {model_name} raised: {exc}[/red]")

    return sorted(all_results, key=lambda r: r.mrr, reverse=True)


# ---------------------------------------------------------------------------
# Ground truth generation
# ---------------------------------------------------------------------------


def _call_claude(prompt: str, timeout: int = _CLAUDE_TIMEOUT) -> str | None:
    """Call `claude -p` with CLAUDECODE unset. Returns stdout or None."""
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
    """Ask Claude to generate *queries_per_note* search queries for the note."""
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

    prompt = (
        f"You are generating evaluation queries for a semantic search benchmark.\n\n"
        f"Below is a vault note. Generate exactly {queries_per_note} distinct search "
        f"queries that a developer would type to find this specific note.\n\n"
        f"Rules:\n"
        f"- Vary specificity: include at least one broad and one specific query\n"
        f"- Use natural language (not keywords only)\n"
        f"- Do NOT include the exact note title as a query\n"
        f'- Return ONLY a JSON object: {{"queries": ["q1", "q2", ...]}}\n\n'
        f"Note title: {title}\n"
        f"Tags: {tags_str}\n"
        f"Content snippet:\n{body[:800]}\n"
    )

    raw = _call_claude(prompt)
    if not raw:
        return []

    json_match = re.search(r'\{[^{}]*"queries"[^{}]*\}', raw, re.DOTALL)
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
        task = progress.add_task("Generating queries via Claude", total=len(sample))
        for note_path in sample:
            queries = generate_queries_for_note(note_path, queries_per_note)
            if queries:
                items.append(EvalItem(stem=note_path.stem, path=str(note_path), queries=queries))
            else:
                failed += 1
            progress.advance(task)

    if failed:
        console.print(f"[yellow]Warning: {failed} notes failed query generation[/yellow]")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    data = [{"stem": i.stem, "path": i.path, "queries": i.queries} for i in items]
    output_file.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"[green]Saved {len(items)} eval items → {output_file}[/green]")
    return items


def load_ground_truth(queries_file: Path) -> list[EvalItem]:
    """Load ground-truth items from a YAML file."""
    raw = yaml.safe_load(queries_file.read_text(encoding="utf-8"))
    return [
        EvalItem(stem=e["stem"], path=e["path"], queries=e["queries"]) for e in raw
    ]


# ---------------------------------------------------------------------------
# Display (Rich terminal table)
# ---------------------------------------------------------------------------


def display_results(results: list[ComboResult], top_k: int) -> None:
    """Render a Rich table comparing all model × chunking combinations."""
    if not results:
        console.print("[yellow]No results to display.[/yellow]")
        return

    table = Table(
        title=f"Embedding Evaluation — Recall & MRR (top_k={top_k})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="bold", no_wrap=True)
    table.add_column("Chunking", no_wrap=True)
    table.add_column("R@1", justify="right")
    table.add_column("R@5", justify="right")
    table.add_column(f"R@{top_k}", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Idx(s)", justify="right", style="dim")
    table.add_column("Q/s", justify="right")
    table.add_column("Chunks", justify="right", style="dim")

    best_mrr = results[0].mrr if results else 0.0
    best_qps = max((r.queries_per_sec for r in results), default=0.0)

    def fmt(v: float) -> str:
        return f"{v:.3f}"

    for res in results:
        model_short = res.model.split("/")[-1]
        mrr_str = fmt(res.mrr)
        qps_str = f"{res.queries_per_sec:.1f}"
        if res.mrr == best_mrr:
            mrr_str = f"[bold green]{mrr_str}[/bold green]"
        if res.queries_per_sec == best_qps:
            qps_str = f"[bold yellow]{qps_str}[/bold yellow]"

        table.add_row(
            model_short,
            res.chunking,
            fmt(res.recall_at_1),
            fmt(res.recall_at_5),
            fmt(res.recall_at_k),
            mrr_str,
            f"{res.index_time_s:.1f}",
            qps_str,
            str(res.chunk_count),
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Save results as JSON
# ---------------------------------------------------------------------------


def save_json_results(results: list[ComboResult], output_path: Path, metadata: dict[str, Any]) -> None:
    """Save evaluation results as JSON."""
    out_data: dict[str, Any] = {
        "metadata": metadata,
        "results": [
            {
                "model": r.model,
                "chunking": r.chunking,
                "recall_at_1": r.recall_at_1,
                "recall_at_5": r.recall_at_5,
                f"recall_at_{r.top_k}": r.recall_at_k,
                "mrr": r.mrr,
                "total_queries": r.total_queries,
                "top_k": r.top_k,
                "index_time_s": round(r.index_time_s, 3),
                "query_time_s": round(r.query_time_s, 3),
                "queries_per_sec": round(r.queries_per_sec, 2),
                "chunk_count": r.chunk_count,
            }
            for r in results
        ],
    }
    output_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def generate_html_report(
    results: list[ComboResult],
    output_path: Path,
    top_k: int,
    metadata: dict[str, Any],
) -> None:
    """Generate a self-contained HTML report with charts and rankings.

    Args:
        results: Sorted ComboResults (best MRR first).
        output_path: Where to write the .html file.
        top_k: The K used for Recall@K.
        metadata: Run parameters dict.
    """
    if not results:
        return

    generated_at = metadata.get("generated_at", datetime.datetime.now().isoformat())
    notes_count = metadata.get("notes_sampled", "?")
    queries_count = metadata.get("total_queries", "?")

    # Prepare chart data
    labels = [f"{r.model.split('/')[-1]}\n{r.chunking}" for r in results]
    labels_js = json.dumps(labels)
    mrr_data = json.dumps([round(r.mrr, 4) for r in results])
    r1_data = json.dumps([round(r.recall_at_1, 4) for r in results])
    r5_data = json.dumps([round(r.recall_at_5, 4) for r in results])
    rk_data = json.dumps([round(r.recall_at_k, 4) for r in results])
    qps_data = json.dumps([round(r.queries_per_sec, 2) for r in results])
    idx_data = json.dumps([round(r.index_time_s, 2) for r in results])

    # Scatter: MRR vs queries_per_sec
    scatter_data = json.dumps([
        {"x": round(r.queries_per_sec, 2), "y": round(r.mrr, 4),
         "label": f"{r.model.split('/')[-1]}/{r.chunking}"}
        for r in results
    ])

    # Medal emojis for top 3
    medals = ["🥇", "🥈", "🥉"]

    # Build rankings cards HTML
    ranking_cards = ""
    for i, res in enumerate(results[:3]):
        medal = medals[i] if i < len(medals) else f"#{i+1}"
        short = res.model.split("/")[-1]
        ranking_cards += f"""
        <div class="rank-card rank-{i+1}">
          <div class="medal">{medal}</div>
          <div class="rank-model">{short}</div>
          <div class="rank-chunking">{res.chunking}</div>
          <div class="rank-metrics">
            <span class="metric-pill">MRR <strong>{res.mrr:.3f}</strong></span>
            <span class="metric-pill">R@1 <strong>{res.recall_at_1:.3f}</strong></span>
            <span class="metric-pill">Q/s <strong>{res.queries_per_sec:.1f}</strong></span>
          </div>
        </div>
        """

    # Build full results table HTML
    table_rows = ""
    for i, res in enumerate(results):
        short = res.model.split("/")[-1]
        rank = i + 1
        best_class = " best-row" if i == 0 else ""
        table_rows += f"""
        <tr class="result-row{best_class}">
          <td class="rank">#{rank}</td>
          <td class="model-name" title="{res.model}">{short}</td>
          <td>{res.chunking}</td>
          <td class="metric">{res.recall_at_1:.3f}</td>
          <td class="metric">{res.recall_at_5:.3f}</td>
          <td class="metric">{res.recall_at_k:.3f}</td>
          <td class="metric highlight">{res.mrr:.3f}</td>
          <td class="metric">{res.index_time_s:.1f}s</td>
          <td class="metric">{res.queries_per_sec:.1f}</td>
          <td class="dim">{res.chunk_count:,}</td>
          <td class="dim">{res.total_queries:,}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Embedding Eval — Claude Vault</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --surface2: #1c2128;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --orange: #db6d28;
      --purple: #bc8cff;
      --gold: #ffd700;
      --silver: #c0c0c0;
      --bronze: #cd7f32;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
      margin-bottom: 2rem;
    }}
    header h1 {{
      font-size: 1.6rem;
      color: var(--accent);
      letter-spacing: -0.5px;
    }}
    header .subtitle {{
      color: var(--muted);
      font-size: 0.8rem;
      margin-top: 0.4rem;
    }}
    .meta-pills {{
      display: flex;
      gap: 0.75rem;
      margin-top: 0.75rem;
      flex-wrap: wrap;
    }}
    .meta-pill {{
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.2rem 0.6rem;
      font-size: 0.75rem;
      color: var(--muted);
    }}
    .meta-pill strong {{ color: var(--text); }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{
      font-size: 1rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 1rem;
    }}
    /* Rankings */
    .rankings {{
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    .rank-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      flex: 1;
      min-width: 200px;
    }}
    .rank-1 {{ border-color: var(--gold); }}
    .rank-2 {{ border-color: var(--silver); }}
    .rank-3 {{ border-color: var(--bronze); }}
    .medal {{ font-size: 2rem; margin-bottom: 0.5rem; }}
    .rank-model {{ font-weight: bold; color: var(--accent); font-size: 0.95rem; }}
    .rank-chunking {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.2rem; }}
    .rank-metrics {{ display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }}
    .metric-pill {{
      background: var(--surface2);
      border-radius: 4px;
      padding: 0.2rem 0.5rem;
      font-size: 0.75rem;
      color: var(--muted);
    }}
    .metric-pill strong {{ color: var(--green); }}
    /* Charts */
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 1.25rem;
    }}
    .chart-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
    }}
    .chart-card h3 {{
      font-size: 0.85rem;
      color: var(--muted);
      margin-bottom: 1rem;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .chart-wrap {{
      position: relative;
      height: 260px;
    }}
    /* Table */
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }}
    th {{
      background: var(--surface2);
      color: var(--muted);
      text-transform: uppercase;
      font-size: 0.7rem;
      letter-spacing: 0.5px;
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--border);
      text-align: right;
      white-space: nowrap;
    }}
    th:first-child, th:nth-child(2), th:nth-child(3) {{ text-align: left; }}
    td {{
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--border);
      text-align: right;
    }}
    td:first-child, td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
    tr:last-child td {{ border-bottom: none; }}
    .best-row td {{ background: rgba(63, 185, 80, 0.06); }}
    .rank {{ color: var(--muted); }}
    .model-name {{ color: var(--accent); font-weight: bold; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .metric {{ font-variant-numeric: tabular-nums; }}
    .highlight {{ color: var(--green); font-weight: bold; }}
    .dim {{ color: var(--muted); }}
    tr:hover td {{ background: var(--surface2); }}
  </style>
</head>
<body>

<header>
  <h1>⚡ Embedding Evaluation — Claude Vault</h1>
  <div class="subtitle">Model × Chunking benchmark using Claude-generated ground truth</div>
  <div class="meta-pills">
    <span class="meta-pill">Generated <strong>{generated_at[:19]}</strong></span>
    <span class="meta-pill">Notes sampled <strong>{notes_count}</strong></span>
    <span class="meta-pill">Total queries <strong>{queries_count}</strong></span>
    <span class="meta-pill">top_k <strong>{top_k}</strong></span>
    <span class="meta-pill">Combos <strong>{len(results)}</strong></span>
  </div>
</header>

<section>
  <h2>🏆 Top Rankings</h2>
  <div class="rankings">
    {ranking_cards}
  </div>
</section>

<section>
  <h2>📊 Charts</h2>
  <div class="charts-grid">
    <div class="chart-card">
      <h3>MRR (Mean Reciprocal Rank) ↑</h3>
      <div class="chart-wrap"><canvas id="mrrChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Recall@1 ↑</h3>
      <div class="chart-wrap"><canvas id="r1Chart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Recall@{top_k} ↑</h3>
      <div class="chart-wrap"><canvas id="rkChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Speed — Queries / Second ↑</h3>
      <div class="chart-wrap"><canvas id="qpsChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Index Time (seconds) ↓</h3>
      <div class="chart-wrap"><canvas id="idxChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Quality vs Speed (MRR vs Q/s)</h3>
      <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
    </div>
  </div>
</section>

<section>
  <h2>📋 Full Results</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th>Model</th>
          <th>Chunking</th>
          <th>R@1</th>
          <th>R@5</th>
          <th>R@{top_k}</th>
          <th>MRR</th>
          <th>Idx Time</th>
          <th>Q/s</th>
          <th>Chunks</th>
          <th>Queries</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>
</section>

<script>
const LABELS = {labels_js};
const palette = [
  '#58a6ff','#3fb950','#d29922','#f78166','#bc8cff',
  '#79c0ff','#56d364','#e3b341','#ffa198','#d2a8ff',
];

function barConfig(label, data, color) {{
  return {{
    type: 'bar',
    data: {{
      labels: LABELS,
      datasets: [{{ label, data, backgroundColor: color + 'bb', borderColor: color, borderWidth: 1 }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e', font: {{ size: 10 }} }} }},
        y: {{ grid: {{ color: '#30363d' }}, ticks: {{ color: '#e6edf3', font: {{ size: 9 }} }} }}
      }}
    }}
  }};
}}

new Chart(document.getElementById('mrrChart'), barConfig('MRR', {mrr_data}, '#3fb950'));
new Chart(document.getElementById('r1Chart'),  barConfig('R@1', {r1_data}, '#58a6ff'));
new Chart(document.getElementById('rkChart'),  barConfig('R@{top_k}', {rk_data}, '#bc8cff'));
new Chart(document.getElementById('qpsChart'), barConfig('Q/s', {qps_data}, '#d29922'));
new Chart(document.getElementById('idxChart'), barConfig('Idx(s)', {idx_data}, '#f78166'));

// Scatter: quality vs speed
const scatterRaw = {scatter_data};
new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: [{{
      label: 'Model × Chunking',
      data: scatterRaw,
      backgroundColor: scatterRaw.map((_, i) => palette[i % palette.length] + 'cc'),
      pointRadius: 8,
      pointHoverRadius: 10,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => `${{ctx.raw.label}}: MRR=${{ctx.raw.y.toFixed(3)}} Q/s=${{ctx.raw.x.toFixed(1)}}`
        }}
      }}
    }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Queries / Second', color: '#8b949e' }}, grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e' }} }},
      y: {{ title: {{ display: true, text: 'MRR', color: '#8b949e' }}, grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the eval pipeline."""
    parser = argparse.ArgumentParser(
        description="Embedding evaluation harness for Claude Vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--generate", action="store_true", default=False,
                        help="Generate ground-truth queries via Claude (even if queries file exists).")
    parser.add_argument("--eval", action="store_true", default=False,
                        help="Run evaluation only (skip query generation).")
    parser.add_argument("--notes", type=int, default=_DEFAULT_NOTES_SAMPLE, metavar="N",
                        help=f"Notes to sample for ground truth (default: {_DEFAULT_NOTES_SAMPLE}).")
    parser.add_argument("--queries-per-note", type=int, default=_DEFAULT_QUERIES_PER_NOTE, metavar="K",
                        help=f"Queries per note (default: {_DEFAULT_QUERIES_PER_NOTE}).")
    parser.add_argument("--queries-file", type=Path, default=_DEFAULT_QUERIES_FILE, metavar="FILE",
                        help=f"YAML ground-truth file (default: {_DEFAULT_QUERIES_FILE}).")
    parser.add_argument("--models", default=",".join(_DEFAULT_MODELS), metavar="M1,M2",
                        help="Comma-separated fastembed model IDs.")
    parser.add_argument("--chunking", default=",".join(_DEFAULT_CHUNKING), metavar="C1,C2",
                        help="Chunking strategies: whole, paragraph, sliding_SIZE_OVERLAP.")
    parser.add_argument("--top-k", type=int, default=_DEFAULT_TOP_K, metavar="K",
                        help=f"Evaluate Recall@K (default: {_DEFAULT_TOP_K}).")
    parser.add_argument("--workers", type=int, default=_DEFAULT_WORKERS, metavar="N",
                        help=f"Parallel threads — one per model (default: {_DEFAULT_WORKERS}).")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for note sampling (default: 42).")
    parser.add_argument("--output", type=Path, default=None, metavar="FILE",
                        help="Base path for output files (auto-timestamped if omitted).")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    chunking_strategies = [c.strip() for c in args.chunking.split(",") if c.strip()]
    queries_file: Path = args.queries_file

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
            console.print(f"[red]Queries file not found: {queries_file}[/red]")
            sys.exit(1)
        eval_items = load_ground_truth(queries_file)
        console.print(f"\n[dim]Loaded {len(eval_items)} eval items from {queries_file}[/dim]")

    if not eval_items:
        console.print("[red]No eval items — cannot run evaluation.[/red]")
        sys.exit(1)

    # Phase 2: Evaluate
    if need_eval:
        total_queries = sum(len(i.queries) for i in eval_items)
        console.print(f"\n[bold]Phase 2: Evaluation matrix[/bold]")
        console.print(f"  Models:   {models}")
        console.print(f"  Chunking: {chunking_strategies}")
        console.print(f"  top_k:    {args.top_k}  workers: {args.workers}\n")

        generated_at = datetime.datetime.now().isoformat(timespec="seconds")

        results = run_evaluation(
            eval_items=eval_items,
            models=models,
            chunking_strategies=chunking_strategies,
            top_k=args.top_k,
            max_workers=args.workers,
        )

        display_results(results, args.top_k)

        if not results:
            return

        # Determine output base path (auto-timestamp if not specified)
        if args.output:
            base_path = args.output
        else:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base_path = vault_common.VAULT_ROOT / f"embed_eval_{ts}"

        json_path = base_path.with_suffix(".json")
        html_path = base_path.with_suffix(".html")

        metadata: dict[str, Any] = {
            "generated_at": generated_at,
            "notes_sampled": len(eval_items),
            "total_queries": total_queries,
            "models": models,
            "chunking_strategies": chunking_strategies,
            "top_k": args.top_k,
            "workers": args.workers,
        }

        save_json_results(results, json_path, metadata)
        console.print(f"[green]Results saved  → {json_path}[/green]")

        generate_html_report(results, html_path, args.top_k, metadata)
        console.print(f"[green]HTML report    → {html_path}[/green]")


if __name__ == "__main__":
    main()
