"""Shared constants, config helpers, and the search-result envelope (ARC-005).

Extracted from ``vault_search.py``. Holds the cross-cutting pieces every
backend/mode submodule needs: the default model id, the valid-backend
frozenset, the env-var prefix, the ``_configured_search_backend`` config
reader (pure config, no embedding machinery — keeps ``embeddings`` and the
routing layer decoupled), the fastembed model lock, and
``SearchResultEnvelope``.
"""

from __future__ import annotations

import threading

import vault_common

_DEFAULT_MODEL: str = vault_common.get_config(
    "embeddings", "model", "BAAI/bge-small-en-v1.5"
)

_VALID_BACKENDS: frozenset[str] = frozenset({"auto", "par-mem", "embeddings", "none"})

_ENV_PREFIX = "VAULT_SEARCH_"

# ENH-003: serialises embed() on the shared cached fastembed model so the
# summarizer's max_parallel fan-out is safe. Lives here (not in embeddings.py)
# so it is importable without pulling sqlite_vec / fastembed.
_EMBED_MODEL_LOCK = threading.Lock()


def _configured_search_backend() -> str:
    """Return the validated ``search.backend`` config value (default: auto)."""
    value = vault_common.get_config("search", "backend", "auto")
    normalized = str(value).strip().lower() if value is not None else "auto"
    return normalized if normalized in _VALID_BACKENDS else "auto"


# ARC-031: ``SearchResultEnvelope`` replaces the ``global LAST_BACKEND`` module
# attribute. The global conflated "which backend served the most recent call"
# into process-wide state, making it unsafe to call ``search()`` concurrently
# (two threads would clobber each other's backend label) and forcing every
# caller that wanted the backend to first call ``search()`` and then read the
# global. The envelope returns both pieces from a single call, and the
# ``score_kind`` discriminator lets callers apply ``min_score`` correctly
# (cosines vs RRF rank-fusion values are not comparable on the same scale).
class SearchResultEnvelope(tuple):
    """Named-tuple-style envelope: ``(results, backend, score_kind)``.

    ``backend`` is one of ``"par-mem" | "embeddings" | "none"``.
    ``score_kind`` is ``"cosine"`` for the embeddings backend, ``"rrf"`` for
    par-mem, and ``None`` when no results were produced (``backend == "none"``).
    """

    __slots__ = ()

    def __new__(
        cls,
        results: list[dict[str, object]],
        backend: str,
        score_kind: str | None,
    ) -> SearchResultEnvelope:
        return tuple.__new__(cls, (results, backend, score_kind))

    @property
    def results(self) -> list[dict[str, object]]:
        """The ranked result rows from the search."""
        return self[0]  # type: ignore[return-value]

    @property
    def backend(self) -> str:
        """Name of the backend that produced the results."""
        return self[1]  # type: ignore[return-value]

    @property
    def score_kind(self) -> str | None:
        """Score semantics of ``results`` (e.g. ``"cosine"``) or ``None`` for unscored modes."""
        return self[2]  # type: ignore[return-value]
