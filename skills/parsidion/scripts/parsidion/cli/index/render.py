"""Markdown rendering for TAGS.md and per-folder MANIFEST.md (ARC-005).

Extracted from ``update_index.py``. Re-exported by the entry shim so
``update_index.build_tags_md`` and ``update_index.build_manifests`` keep
resolving for tests and other callers.

ARC-2XX (card: hoist the row engine into core + docs from note_index): the
emitters now live in ``parsidion.core.note_docs`` together with the
CLAUDE.md emitter and the row-driven doc generation; this module re-exports
them so every ``cli.index.*`` consumer and the ``update_index`` shim keep
resolving the same names.

Stdlib-only at module load.
"""

from __future__ import annotations

from parsidion.core.note_docs import build_claude_md, build_manifests, build_tags_md

__all__: list[str] = [
    "build_claude_md",
    "build_manifests",
    "build_tags_md",
]
