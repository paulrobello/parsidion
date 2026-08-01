"""Post-index ``build_graph.py`` invocation (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index._find_build_graph_script`` and
``update_index._rebuild_graph`` keep resolving for tests and other callers.

Stdlib-only at module load.

Path note: this module lives at ``<scripts>/cli/index/graph.py`` (three
levels below ``scripts/``). The original ``update_index.py`` used
``Path(__file__).parent`` and ``Path(__file__).resolve().parents[3]`` to
locate ``build_graph.py``; the equivalent resolutions from here use
``parents[2]`` (co-installed: ``<scripts>/build_graph.py``) and
``parents[5]`` (repo-root: ``<repo>/scripts/build_graph.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _find_build_graph_script() -> Path | None:
    """Locate build_graph.py in known locations.

    Checks two candidates in order:
    1. ``<scripts>/build_graph.py`` (co-installed scenario — three levels
       up from this module).
    2. ``<repo-root>/scripts/build_graph.py`` (source-repo scenario).

    Returns:
        Path to the script if found, else None.
    """
    # Candidate 1: co-installed — <scripts>/build_graph.py. From
    # ``<scripts>/cli/index/graph.py`` that is three parents up.
    candidate = Path(__file__).resolve().parents[2] / "build_graph.py"
    if candidate.exists():
        return candidate
    # Candidate 2: repo-root/scripts/build_graph.py. From
    # ``<scripts>/cli/index/graph.py`` the repo root is five parents up:
    # parents[0]=index/ [1]=cli/ [2]=scripts/ [3]=parsidion/ [4]=skills/
    # [5]=<repo-root>/.
    candidate = Path(__file__).resolve().parents[5] / "scripts" / "build_graph.py"
    if candidate.exists():
        return candidate
    return None


def _rebuild_graph(include_daily: bool, incremental: bool = False) -> None:
    """Run build_graph.py synchronously and print its output.

    Args:
        include_daily: When True, pass ``--include-daily`` to build_graph.py;
            when False, pass ``--no-daily``. build_graph.py defaults to
            ``include_daily=True`` (build_graph.py:44), so omitting the flag
            entirely produces the *with*-Daily behavior regardless of the
            caller's intent — DOC-003 caught this exact bug (the message said
            'without Daily notes' while the build was including them).
        incremental: When True, pass ``--incremental`` (ENH-002) so build_graph.py
            reuses the previous graph.json and recomputes only changed notes.
            build_graph.py falls back to a full rebuild on any compatibility
            mismatch, so passing this is always safe.
    """
    graph_script = _find_build_graph_script()
    if graph_script is None:
        print(
            "Graph rebuild skipped: build_graph.py not found. "
            "Run from the parsidion repo or co-install build_graph.py.",
            file=sys.stderr,
        )
        return

    cmd = ["uv", "run", "--no-project", str(graph_script)]
    # DOC-003: pass --no-daily when False — build_graph.py defaults to
    # include_daily=True, so without an explicit flag the index would include
    # Daily notes regardless of the caller's request.
    cmd.append("--include-daily" if include_daily else "--no-daily")
    if incremental:
        cmd.append("--incremental")

    mode = "incremental" if incremental else "full"
    print(
        f"Graph: rebuilding graph.json ({mode}, {'with' if include_daily else 'without'} Daily notes)..."
    )
    # QA-005: bound the graph rebuild — a hung child stalls the summarizer
    # mid-run and leaves the index stale with no error. 300 s is generous for
    # a 5k-node vault (measured cold-cache rebuild ~30 s on the dev vault).
    try:
        result = subprocess.run(cmd, capture_output=False, timeout=300)
    except subprocess.TimeoutExpired:
        print(
            "Graph rebuild timed out after 300s — graph.json left stale. "
            "Run `make graph` manually to investigate.",
            file=sys.stderr,
        )
        return
    if result.returncode != 0:
        print(f"Graph rebuild failed (exit {result.returncode})", file=sys.stderr)
