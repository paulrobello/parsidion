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
"""Embedding evaluation harness for Parsidion vault.

Compares embedding models x chunking strategies using Claude-generated
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

    # Cap index size (default 200; 0 = all vault notes):
    uv run embed_eval.py --max-index-notes 100
    uv run embed_eval.py --max-index-notes 0  # full vault (slow with paragraph)

This is the main orchestrator that delegates to three phase sub-scripts:
  - embed_eval_common.py   -- shared types, constants, pure utilities
  - embed_eval_generate.py -- ground-truth query generation (Phase 1)
  - embed_eval_run.py      -- embedding + search evaluation (Phase 2)
  - embed_eval_report.py   -- Rich table + HTML chart rendering (Phase 3)
"""

import argparse
import datetime
import sys
from pathlib import Path
from typing import Any

# Ensure sibling scripts are importable (e.g. vault_common)
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import vault_common  # noqa: E402
from embed_eval_common import (  # noqa: E402
    DEFAULT_CHUNKING,
    DEFAULT_MODELS,
    DEFAULT_NOTES_SAMPLE,
    DEFAULT_QUERIES_FILE,
    DEFAULT_QUERIES_PER_NOTE,
    DEFAULT_TOP_K,
    DEFAULT_WORKERS,
    console,
)
from embed_eval_generate import generate_ground_truth, load_ground_truth  # noqa: E402
from embed_eval_report import display_results, generate_html_report, save_json_results  # noqa: E402
from embed_eval_run import run_evaluation  # noqa: E402


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and run the eval pipeline."""
    parser = argparse.ArgumentParser(
        description="Embedding evaluation harness for Parsidion vault.",
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
        help="Run evaluation only (skip query generation).",
    )
    parser.add_argument(
        "--notes",
        type=int,
        default=DEFAULT_NOTES_SAMPLE,
        metavar="N",
        help=f"Notes to sample for ground truth (default: {DEFAULT_NOTES_SAMPLE}).",
    )
    parser.add_argument(
        "--queries-per-note",
        type=int,
        default=DEFAULT_QUERIES_PER_NOTE,
        metavar="K",
        help=f"Queries per note (default: {DEFAULT_QUERIES_PER_NOTE}).",
    )
    parser.add_argument(
        "--queries-file",
        type=Path,
        default=DEFAULT_QUERIES_FILE,
        metavar="FILE",
        help=f"YAML ground-truth file (default: {DEFAULT_QUERIES_FILE}).",
    )
    parser.add_argument(
        "--models",
        default=",".join(DEFAULT_MODELS),
        metavar="M1,M2",
        help="Comma-separated fastembed model IDs.",
    )
    parser.add_argument(
        "--chunking",
        default=",".join(DEFAULT_CHUNKING),
        metavar="C1,C2",
        help="Chunking strategies: whole, paragraph, sliding_SIZE_OVERLAP.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_TOP_K,
        metavar="K",
        help=f"Evaluate Recall@K (default: {DEFAULT_TOP_K}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        metavar="N",
        help=f"Parallel threads — one per model (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--max-index-notes",
        type=int,
        default=200,
        metavar="N",
        help="Max notes to index (0 = all vault notes). Eval notes always "
        "included; remaining slots filled with random distractors (default: 200).",
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
        help="Base path for output files (auto-timestamped if omitted).",
    )
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    chunking_strategies = [c.strip() for c in args.chunking.split(",") if c.strip()]
    queries_file: Path = args.queries_file

    need_generate = args.generate or (not args.eval and not queries_file.exists())
    need_eval = args.eval or not args.generate

    # Phase 1: Generate
    if need_generate:
        console.print("\n[bold]Phase 1: Generating ground-truth queries[/bold]")
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
        console.print(
            f"\n[dim]Loaded {len(eval_items)} eval items from {queries_file}[/dim]"
        )

    if not eval_items:
        console.print("[red]No eval items — cannot run evaluation.[/red]")
        sys.exit(1)

    # Phase 2: Evaluate
    if need_eval:
        total_queries = sum(len(i.queries) for i in eval_items)
        console.print("\n[bold]Phase 2: Evaluation matrix[/bold]")
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
            max_index_notes=args.max_index_notes,
            seed=args.seed,
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
        console.print(f"[green]Results saved  -> {json_path}[/green]")

        generate_html_report(results, html_path, args.top_k, metadata)
        console.print(f"[green]HTML report    -> {html_path}[/green]")


if __name__ == "__main__":
    main()
