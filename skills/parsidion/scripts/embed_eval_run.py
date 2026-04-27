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
"""Phase 2: Embedding evaluation runner.

Builds in-memory sqlite-vec indexes for each model x chunking combination,
runs ground-truth queries, and computes retrieval metrics (Recall@K, MRR).
"""

import concurrent.futures
import os
import random
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import sqlite_vec  # type: ignore[import-untyped]
from fastembed import TextEmbedding  # type: ignore[import-untyped]
from rich.progress import (  # type: ignore[import-untyped]
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

# Ensure sibling scripts are importable
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import vault_common  # noqa: E402
from embed_eval_common import (  # noqa: E402
    DEFAULT_WORKERS,
    ComboResult,
    EvalItem,
    _pack_vec,
    chunk_note,
    console,
)


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
            [
                (stem, _pack_vec(list(vec)))
                for stem, vec in zip(stems, vectors, strict=False)
            ],
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
# Parallel worker -- one thread per model, all chunkings in serial
# ---------------------------------------------------------------------------


def _eval_model_combos(
    model_name: str,
    chunking_strategies: list[str],
    note_paths: list[Path],
    eval_items: list[EvalItem],
    top_k: int,
    progress_queue: Any,  # multiprocessing.Queue or None
    onnx_threads: int | None = None,
) -> list[ComboResult]:
    """Load model once, then evaluate all chunking strategies for it.

    Designed to run in a ThreadPoolExecutor worker -- each thread handles
    one model and iterates over all chunkings serially, avoiding redundant
    model loads.

    Args:
        model_name: fastembed model ID.
        onnx_threads: ONNX Runtime thread count per model.
        chunking_strategies: List of chunking strategy strings.
        note_paths: Note paths to embed.
        eval_items: Ground-truth items.
        top_k: Recall@K cutoff.
        progress_queue: If provided, put (model_name, chunking) tuples when done.

    Returns:
        List of ComboResult (one per chunking strategy).
    """
    try:
        kwargs: dict[str, object] = {"model_name": model_name}
        if onnx_threads is not None:
            kwargs["threads"] = onnx_threads
        model = TextEmbedding(**kwargs)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
    max_workers: int = DEFAULT_WORKERS,
    max_index_notes: int = 200,
    seed: int = 42,
) -> list[ComboResult]:
    """Run the full model x chunking evaluation matrix in parallel.

    One thread per model; each thread evaluates all chunking strategies
    serially (model loaded once per thread).

    Args:
        eval_items: Ground-truth items.
        models: List of fastembed model IDs.
        chunking_strategies: List of chunking strategy names.
        top_k: Recall@K cutoff.
        max_workers: Maximum parallel threads (one per model).
        max_index_notes: Cap total indexed notes (0 = all). Eval notes are
            always included; remaining slots filled with random distractors.
        seed: Random seed for distractor sampling.

    Returns:
        List of ComboResult sorted by MRR descending.
    """
    # Index vault notes -- eval notes always included; distractors fill the rest.
    all_notes: list[Path] = vault_common.all_vault_notes()

    if not all_notes:
        console.print("[red]No vault notes found.[/red]")
        return []

    eval_paths: set[Path] = {Path(item.path).resolve() for item in eval_items}
    if max_index_notes > 0 and len(all_notes) > max_index_notes:
        distractors = [p for p in all_notes if p.resolve() not in eval_paths]
        rng = random.Random(seed)
        sampled_distractors = rng.sample(
            distractors, min(max_index_notes - len(eval_paths), len(distractors))
        )
        note_paths = [
            p for p in all_notes if p.resolve() in eval_paths
        ] + sampled_distractors
        index_note = f"{len(note_paths)} (capped from {len(all_notes)})"
    else:
        note_paths = all_notes
        index_note = str(len(note_paths))

    total_combos = len(models) * len(chunking_strategies)
    total_queries = sum(len(i.queries) for i in eval_items)
    console.print(
        f"\n[bold]Running {total_combos} combinations in parallel[/bold] "
        f"({len(eval_items)} eval notes, {index_note} indexed, "
        f"{total_queries} queries, {len(models)} threads)\n"
    )

    all_results: list[ComboResult] = []
    workers = min(max_workers, len(models))
    # Limit ONNX threads per model to prevent CPU oversubscription.
    cpu_count = os.cpu_count() or 4
    onnx_threads: int | None = max(1, cpu_count // workers) if workers > 1 else None

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Evaluating combos", total=total_combos)

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
                    None,  # no queue needed -- we poll futures
                    onnx_threads,
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
                            f"  [green]v[/green] {short}/{r.chunking} "
                            f"MRR={r.mrr:.3f} "
                            f"idx={r.index_time_s:.1f}s "
                            f"qry={r.queries_per_sec:.1f}q/s"
                        )
                except Exception as exc:  # noqa: BLE001
                    console.log(f"[red]  Model {model_name} raised: {exc}[/red]")

    return sorted(all_results, key=lambda r: r.mrr, reverse=True)
