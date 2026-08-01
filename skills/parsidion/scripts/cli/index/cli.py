"""Argparse parser for the ``update_index`` CLI (ARC-005).

Extracted from ``update_index.py``. The ``main()`` entry point stays in
the shim (it weaves together the singleton guard, the inline
``__file__``-relative ``build_embeddings.py`` discovery, and the
par-mem/embeddings spawn); only the parser extraction moves here.
Re-exported by the entry shim so ``update_index._parse_args`` keeps
resolving for tests and other callers.

Stdlib-only at module load.
"""

from __future__ import annotations

import argparse


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for update_index."""
    parser = argparse.ArgumentParser(
        description="Rebuild the vault index (CLAUDE.md, MANIFEST.md, note_index DB).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vault",
        "-V",
        type=str,
        help="Vault name or path (default: current project-local or default vault)",
    )
    parser.add_argument(
        "--rebuild-graph",
        action="store_true",
        default=False,
        help="Also rebuild visualizer graph.json after the index update",
    )
    parser.add_argument(
        "--graph-include-daily",
        action="store_true",
        default=False,
        help="Include Daily folder notes in the graph (only used with --rebuild-graph)",
    )
    parser.add_argument(
        "--graph-incremental",
        action="store_true",
        default=False,
        help=(
            "Rebuild graph.json incrementally (ENH-002): reuse the previous "
            "graph and recompute only changed notes. Honoured only with "
            "--rebuild-graph. Also enabled by summarizer.graph_incremental in "
            "config.yaml. build_graph.py falls back to a full rebuild if the "
            "previous graph is missing or was built under different parameters."
        ),
    )
    return parser.parse_args()
