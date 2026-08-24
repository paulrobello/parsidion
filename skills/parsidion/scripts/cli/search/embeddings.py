"""Embeddings-backend machinery: fastembed + sqlite-vec (ARC-005).

Extracted from ``vault_search.py``. Houses the in-process ONNX model cache,
the optional ENH-003 persistent embedding service, and the embeddings-DB
search that serves as the always-on fallback when par-mem is not selected
or not available.

ARC-006: the ``vault_search.py`` entry shim calls ``_search_embeddings``
THROUGH this module (``embeddings._search_embeddings(...)``), so tests
monkeypatch it here — ``cli.search.embeddings._search_embeddings`` — and
the patch resolves at call time.
"""

from __future__ import annotations

import functools
import json
import socket
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

import vault_common
import vault_metrics
import vault_embed_serve
from cli.search._common import (
    _DEFAULT_MODEL,
    _EMBED_MODEL_LOCK,
    _configured_search_backend,
)
from vault_config import apply_decay_score


def _open_db_semantic(db_path: Path) -> sqlite3.Connection:
    """Open embeddings DB with sqlite-vec extension loaded.

    Args:
        db_path: Path to the SQLite embeddings database.

    Returns:
        An open sqlite3.Connection with sqlite-vec loaded.
    """
    # QA-017: shared vec-loading connector from core.vault_metrics; the
    # missing-extension diagnostic stays at this CLI boundary.
    try:
        return vault_metrics.connect_with_vec(db_path)
    except vault_metrics.VecExtensionMissing as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float32 vector as a BLOB for sqlite-vec query parameter.

    Args:
        vec: List of float values.

    Returns:
        Packed binary representation.
    """
    return struct.pack(f"{len(vec)}f", *vec)


def _apply_decay(score: float, mtime: float, now: float) -> float:
    """Apply temporal decay to a semantic search score.

    ARC-023: thin wrapper around ``vault_config.apply_decay_score`` (the
    canonical implementation moved there to break the vault_search ↔
    parmem_backend top-level cycle). Kept as a private alias so existing
    internal call sites and parmem_backend's lazy import (if any older copy of
    the module still references it) keep resolving during the transition.
    New code should call ``vault_config.apply_decay_score`` directly.
    """
    return apply_decay_score(score, mtime, now)


# ENH-003: the fastembed ONNX model is ~67 MB and dominates a search whose real
# work is a sqlite-vec ANN lookup. Cache one instance per model name for the
# process lifetime (maxsize=2 covers the default plus one override), and
# serialise embed() so the shared instance is safe under the summarizer's
# max_parallel fan-out. lru_cache does not memoise exceptions, so a missing
# fastembed still degrades gracefully (the call-site guard below) and is retried
# rather than sticky-cached.


@functools.lru_cache(maxsize=2)
def _get_embedding_model(model_name: str):  # type: ignore[no-untyped-def]
    """Return a process-cached fastembed ``TextEmbedding`` for *model_name*."""
    from fastembed import TextEmbedding  # type: ignore[import-untyped]

    return TextEmbedding(model_name=model_name)


# ---------------------------------------------------------------------------
# ENH-003: optional persistent embedding service (vault_embed_serve.py)
# ---------------------------------------------------------------------------
# AF_UNIX, lives outside the synced vault tree. Contacted only from
# _search_embeddings — i.e. only when par-mem did not serve — and only when
# embeddings.service_enabled is true and the backend is not par-mem-only
# (the user's constraint). A daemon miss is normal: it falls back to the
# in-process cached model, so the service is an optimization, never a
# dependency. Nothing here raises.
_SERVICE_SPAWN_DEBOUNCE_S = 30.0
_last_service_spawn_attempt = 0.0

# scripts/ is three parents up from cli/search/embeddings.py
# (embeddings.py → cli/search/ → cli/ → scripts/).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent


def _embeddings_service_active() -> bool:
    """True only when the persistent embedding service should be used.

    Two hard guards: explicit opt-in via ``embeddings.service_enabled``
    (default false), AND the backend must not be par-mem-only — par-mem serves
    retrieval without local query embeddings, so the service would never be
    consulted and must not run. ``_search_embeddings`` is itself only reached
    when par-mem didn't serve under ``auto``, so the second guard mainly covers
    an explicit ``search.backend: par-mem`` setting.
    """
    if vault_common.get_config("embeddings", "service_enabled", False) is not True:
        return False
    if _configured_search_backend() == "par-mem":
        return False
    return True


def _spawn_service(vault: Path, model_name: str) -> None:
    """Best-effort detached launch of the embed service.

    Debounced so a daemon that fails to start isn't re-spawned every call. A
    no-op when one is already running: the daemon self-guards via its PID file
    (a second launch sees the live PID and exits immediately).
    """
    global _last_service_spawn_attempt
    now = time.monotonic()
    if now - _last_service_spawn_attempt < _SERVICE_SPAWN_DEBOUNCE_S:
        return
    _last_service_spawn_attempt = now
    try:
        script = _SCRIPTS_DIR / "vault_embed_serve.py"
        if not script.exists():
            return
        idle = int(
            vault_common.get_config("embeddings", "service_idle_exit", 600) or 600
        )
        subprocess.Popen(
            [
                str(script),
                "--vault",
                str(vault),
                "--model",
                model_name,
                "--idle-exit",
                str(idle),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=vault_common.env_without_claudecode(),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; in-process fallback covers failure
        print(f"embedding service spawn best-effort: {exc}", file=sys.stderr)
        pass


def _service_embed(
    query: str, model_name: str, vault: Path, timeout: float = 5.0
) -> list[float] | None:
    """Ask the running service to embed *query*; return the vector or None."""
    try:
        path = vault_embed_serve.socket_path(vault)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path))
            s.sendall(
                (json.dumps({"text": query, "model": model_name}) + "\n").encode(
                    "utf-8"
                )
            )
            buf = bytearray()
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                buf.extend(chunk)
                if b"\n" in chunk:
                    break
        payload = json.loads(buf.split(b"\n", 1)[0].decode("utf-8", "replace"))
        vec = payload.get("vector") if isinstance(payload, dict) else None
        if isinstance(vec, list):
            return [float(x) for x in vec]
    except Exception:  # noqa: BLE001 — absence/failure is normal; caller falls back
        return None
    return None


def _embed_query(query: str, model_name: str, vault: Path | None) -> list[float]:
    """Embed *query*: prefer the opt-in service, else the in-process cache.

    The service is tried only when ``_embeddings_service_active`` is true; any
    miss falls back to the cached in-process model (loaded once per process).
    """
    resolved = vault or vault_common.resolve_vault()
    if _embeddings_service_active():
        _spawn_service(resolved, model_name)
        vec = _service_embed(query, model_name, resolved)
        if vec is not None:
            return vec
    model = _get_embedding_model(model_name)
    with _EMBED_MODEL_LOCK:
        embedded = list(model.embed([query]))
    return [float(x) for x in embedded[0]]


def _search_embeddings(
    query: str,
    top: int = 10,
    min_score: float = 0.45,
    model_name: str = _DEFAULT_MODEL,
    vault: Path | None = None,
) -> list[dict[str, object]]:
    """Embeddings-backend semantic search (the always-on fallback path).

    Returns an empty list gracefully when embeddings.db does not exist.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0.0–1.0).
        model_name: fastembed model ID used when the index was built.
        vault: Optional vault path. Defaults to resolve_vault().

    Returns:
        List of result dicts with keys: score, stem, title, folder, tags, path.
        Sorted by score descending.
    """
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    try:
        query_vec = _embed_query(query, model_name, vault)
        query_blob = _pack_vector(list(query_vec))
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    decay_enabled: bool = vault_common.get_config(
        "embeddings",
        "decay_enabled",
        True,
    )
    now = time.time() if decay_enabled else 0.0

    try:
        conn = _open_db_semantic(db_path)
        cursor = conn.execute(
            """
            SELECT stem, path, folder, title, tags,
                   (1.0 - vec_distance_cosine(embedding, ?)) AS score,
                   mtime
            FROM note_embeddings
            ORDER BY score DESC
            LIMIT ?
            """,
            (query_blob, top),
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    results: list[dict[str, object]] = []
    for stem, path, folder, title, tags_str, score, mtime in rows:
        if decay_enabled and mtime:
            score = _apply_decay(score, mtime, now)
        if score < min_score:
            continue
        tags_raw: str = tags_str if isinstance(tags_str, str) else ""
        tags: list[str] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        results.append(
            {
                "score": round(float(score), 4),
                "stem": stem,
                "title": title,
                "folder": folder,
                "tags": tags,
                "path": path,
                "summary": "",
                "note_type": "",
                "project": "",
                "confidence": "",
                "mtime": None,
                "related": [],
                "is_stale": False,
                "incoming_links": 0,
            }
        )

    return results
