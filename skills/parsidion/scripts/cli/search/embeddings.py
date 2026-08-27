"""Embeddings-backend machinery: fastembed + sqlite-vec (ARC-005).

Extracted from ``vault_search.py``. Houses the in-process ONNX model cache,
the optional ENH-003 persistent embedding service, and the embeddings-DB
search that serves as the always-on fallback when parsight is not selected
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

import vault_embed_serve
from core import vault_metrics
from core.vault_config import get_config
from core.vault_fs import (
    release_singleton_lock,
    try_singleton_lock,
    write_hook_event,
)
from core.vault_hooks import env_without_claudecode
from core.vault_path import get_embeddings_db_path, resolve_vault
from cli.search._common import (
    _DEFAULT_MODEL,
    _EMBED_MODEL_LOCK,
    _configured_search_backend,
)
from core.vault_config import apply_decay_score, resolve_decay_params


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


def _apply_decay(
    score: float,
    mtime: float,
    now: float,
    *,
    half_life_days: float | None = None,
    min_factor: float | None = None,
    vault: Path | None = None,
) -> float:
    """Apply temporal decay to a semantic search score.

    ARC-023: thin wrapper around ``vault_config.apply_decay_score`` (the
    canonical implementation moved there to break the vault_search ↔
    parsight_backend top-level cycle). Kept as a private alias so existing
    internal call sites and parsight_backend's lazy import (if any older copy of
    the module still references it) keep resolving during the transition.
    New code should call ``vault_config.apply_decay_score`` directly.
    PRF-103: accepts the decay parameters explicitly so the scoring loop can
    resolve them once per search instead of per row.
    """
    return apply_decay_score(
        score,
        mtime,
        now,
        half_life_days=half_life_days,
        min_factor=min_factor,
        vault=vault,
    )


# ENH-003: the fastembed ONNX model is ~67 MB and dominates a search whose real
# work is ranking rows in SQLite. Cache one instance per model name for the
# process lifetime (maxsize=2 covers the default plus one override), and
# serialise embed() so the shared instance is safe under the summarizer's
# max_parallel fan-out. lru_cache does not memoise exceptions, so a missing
# fastembed still degrades gracefully (the call-site guard below) and is retried
# rather than sticky-cached.
#
# ENH-022: candidate rows come from the sqlite-vec ``vec0`` KNN index
# (``note_vec``, dual-written by build_embeddings) whenever it is present and
# row-count-synced with ``note_embeddings``; otherwise the exact full-table
# cosine scan serves the query (pre-ENH-022 DBs, interrupted builds). See
# ``_fetch_candidate_rows``.


@functools.lru_cache(maxsize=2)
def _get_embedding_model(model_name: str):  # type: ignore[no-untyped-def]
    """Return a process-cached fastembed ``TextEmbedding`` for *model_name*."""
    from fastembed import TextEmbedding  # type: ignore[import-untyped]

    return TextEmbedding(model_name=model_name)


# ---------------------------------------------------------------------------
# ENH-003: optional persistent embedding service (vault_embed_serve.py)
# ---------------------------------------------------------------------------
# AF_UNIX, lives outside the synced vault tree. Contacted only from
# _search_embeddings — i.e. only when parsight did not serve — and only when
# embeddings.service_enabled is true (the ENH-020 default) and the backend is
# not parsight-only (the user's constraint). A daemon miss is normal: it falls
# back to the in-process cached model, so the service is an optimization,
# never a dependency. Nothing here raises.
_SERVICE_SPAWN_DEBOUNCE_S = 30.0
_last_service_spawn_attempt = 0.0
_COLD_LOAD_DEBOUNCE_S = 30.0
_last_cold_load_event = 0.0

# scripts/ is three parents up from cli/search/embeddings.py
# (embeddings.py → cli/search/ → cli/ → scripts/).
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent


def _embeddings_service_active(backend: str | None = None) -> bool:
    """True only when the persistent embedding service should be used.

    Two hard guards: ``embeddings.service_enabled`` (default true since
    ENH-020; set false to opt out), AND the effective backend must not be
    parsight — parsight serves retrieval without local query embeddings, so
    the service would never be consulted and must not run. *backend* is the
    per-call resolved backend (``-B`` override aware); when None the
    ``search.backend`` config value is read. A config of ``parsight`` with a
    CLI-forced ``embeddings`` run means parsight is NOT serving this call,
    so the guard honours the override.
    """
    if get_config("embeddings", "service_enabled", True) is not True:
        return False
    effective = backend if backend is not None else _configured_search_backend()
    if effective == "parsight":
        return False
    return True


def _spawn_service(vault: Path, model_name: str) -> None:
    """Best-effort detached launch of the embed service (ENH-020).

    Single-flight across processes: the spawn runs under a non-blocking
    flock on the service's runtime lock file, so N cold clients launched in
    parallel start at most one daemon — the rest see the lock held, skip,
    and hit the warm socket shortly after. Also debounced in-process so a
    daemon that fails to start isn't re-spawned every call, and a no-op when
    one is already running (the daemon self-guards via its PID file and the
    atomic socket bind).
    """
    global _last_service_spawn_attempt
    now = time.monotonic()
    if now - _last_service_spawn_attempt < _SERVICE_SPAWN_DEBOUNCE_S:
        return
    _last_service_spawn_attempt = now
    lock_fd = try_singleton_lock(vault_embed_serve.lock_path(vault))
    if lock_fd is None:
        # Another cold client is mid-spawn and will bring the socket up.
        return
    try:
        script = _SCRIPTS_DIR / "vault_embed_serve.py"
        if not script.exists():
            return
        idle = int(get_config("embeddings", "service_idle_exit", 600) or 600)
        proc = subprocess.Popen(
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
            env=env_without_claudecode(),
        )
        write_hook_event(
            "EmbedServiceSpawn",
            "embeddings",
            0.0,
            vault=vault,
            model=model_name,
            pid=proc.pid,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort; in-process fallback covers failure
        print(f"embedding service spawn best-effort: {exc}", file=sys.stderr)
    finally:
        # The lock covers the spawn call only — the daemon warms up unheld,
        # so a later caller can re-spawn if it dies.
        release_singleton_lock(lock_fd)


def _note_cold_load(vault: Path, model_name: str) -> None:
    """Log an ``EmbedColdLoad`` hook event (debounced) on a service miss.

    With the service auto-starting (ENH-020), a cold in-process load means
    the daemon was not warm yet or failed to start. The debounce keeps
    ``vault-stats --hooks`` an honest signal without one log line per query
    while the service is down.
    """
    global _last_cold_load_event
    now = time.monotonic()
    if now - _last_cold_load_event < _COLD_LOAD_DEBOUNCE_S:
        return
    _last_cold_load_event = now
    write_hook_event("EmbedColdLoad", "embeddings", 0.0, vault=vault, model=model_name)


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


def _embed_query(
    query: str,
    model_name: str,
    vault: Path | None,
    backend: str | None = None,
) -> list[float]:
    """Embed *query*: prefer the opt-in service, else the in-process cache.

    The service is tried only when ``_embeddings_service_active`` is true; any
    miss falls back to the cached in-process model (loaded once per process).
    *backend* is the per-call resolved search backend (see
    ``_embeddings_service_active``).
    """
    resolved = vault or resolve_vault()
    if _embeddings_service_active(backend):
        # Socket first: with the service default-on (ENH-020) a warm daemon
        # is the common case, and spawning before the check would launch a
        # throwaway duplicate daemon (PID-guarded, it exits — but every
        # short-lived client would pay the launch) on every query.
        vec = _service_embed(query, model_name, resolved)
        if vec is not None:
            return vec
        _spawn_service(resolved, model_name)
        _note_cold_load(resolved, model_name)
    model = _get_embedding_model(model_name)
    with _EMBED_MODEL_LOCK:
        embedded = list(model.embed([query]))
    return [float(x) for x in embedded[0]]


# ENH-022: vec0 KNN candidate fetch with exact-scan fallback. The KNN set is
# materialized in a derived table before the metadata join — vec0's KNN
# constraints (MATCH + k) cannot be planned through an arbitrary join in all
# sqlite-vec releases.
_KNN_SQL = """
    SELECT ne.stem, ne.path, ne.folder, ne.title, ne.tags,
           (1.0 - nv.distance) AS score,
           ne.mtime
    FROM (SELECT rowid, distance FROM note_vec
          WHERE embedding MATCH ? AND k = ?) nv
    JOIN note_embeddings ne ON ne.id = nv.rowid
"""

_SCAN_SQL = """
    SELECT stem, path, folder, title, tags,
           (1.0 - vec_distance_cosine(embedding, ?)) AS score,
           mtime
    FROM note_embeddings
    ORDER BY score DESC
    LIMIT ?
"""

# One-shot notice when the vec0 index cannot serve and the exact scan takes
# over — the same best-effort stderr channel the service spawn uses. One
# line per process, not per query.
_note_vec_fallback_logged = False


def _note_vec_fallback_note(reason: str) -> None:
    """Log (once per process) that search fell back to the exact scan."""
    global _note_vec_fallback_logged
    if _note_vec_fallback_logged:
        return
    _note_vec_fallback_logged = True
    print(
        f"note_vec KNN index unusable ({reason}) — using exact scan fallback",
        file=sys.stderr,
    )


def _vec0_ready(conn: sqlite3.Connection) -> bool:
    """True when note_vec can serve this search (ENH-022).

    The table must exist AND hold exactly as many rows as note_embeddings:
    an absent table means a pre-ENH-022 or not-yet-mirrored DB, and a count
    mismatch means the mirror is out of sync (e.g. an older binary wrote
    note_embeddings alone). Both fall back to the exact scan, which is
    always correct — only slower.
    """
    try:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'note_vec'"
        ).fetchone()
        if present is None:
            return False
        vec_count = conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()
        emb_count = conn.execute("SELECT COUNT(*) FROM note_embeddings").fetchone()
        return bool(
            vec_count is not None
            and emb_count is not None
            and vec_count[0] == emb_count[0]
        )
    except sqlite3.Error:
        return False


def _fetch_candidate_rows(
    conn: sqlite3.Connection, query_blob: bytes, fetch_k: int
) -> list[tuple[str, str, str, str, str, float, float]]:
    """Fetch the *fetch_k* nearest candidate rows for the query blob (ENH-022).

    Prefers the vec0 KNN index (cosine distance, so ``1.0 - distance`` is the
    same score the scan's ``vec_distance_cosine`` produces); falls back to the
    exact full-table cosine scan when the index is absent/out of sync or the
    KNN query itself fails. Both paths return
    ``(stem, path, folder, title, tags, raw cosine score, mtime)`` rows that
    feed the identical ARC-102 decay → min_score → sort → truncate pipeline
    in ``_search_embeddings``.
    """
    if _vec0_ready(conn):
        try:
            return conn.execute(_KNN_SQL, (query_blob, fetch_k)).fetchall()
        except sqlite3.Error as exc:
            _note_vec_fallback_note(f"KNN query failed: {exc}")
    else:
        _note_vec_fallback_note("table missing or out of sync")
    return conn.execute(_SCAN_SQL, (query_blob, fetch_k)).fetchall()


def _search_embeddings(
    query: str,
    top: int = 10,
    min_score: float = 0.45,
    model_name: str = _DEFAULT_MODEL,
    vault: Path | None = None,
    backend: str | None = None,
) -> list[dict[str, object]]:
    """Embeddings-backend semantic search (the always-on fallback path).

    Returns an empty list gracefully when embeddings.db does not exist.

    Args:
        query: Natural language query string.
        top: Maximum number of results to return.
        min_score: Minimum cosine similarity threshold (0.0–1.0).
        model_name: fastembed model ID used when the index was built.
        vault: Optional vault path. Defaults to resolve_vault().
        backend: Per-call resolved search backend (``-B`` override aware);
            forwarded to the service gate so a forced embeddings run uses
            the warm service even when the config backend is parsight.

    Returns:
        List of result dicts with keys: score, stem, title, folder, tags, path.
        Sorted by score descending.
    """
    db_path = get_embeddings_db_path(vault)
    if not db_path.exists():
        return []

    try:
        query_vec = _embed_query(query, model_name, vault, backend)
        query_blob = _pack_vector(list(query_vec))
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    decay_enabled: bool = get_config(
        "embeddings",
        "decay_enabled",
        True,
    )
    now = time.time() if decay_enabled else 0.0
    # PRF-103: resolve the decay parameters once per search — previously
    # apply_decay_score re-read them (two get_config calls, each rebuilding
    # the config tree) for every scored row.
    half_life, min_factor = resolve_decay_params(vault)

    try:
        conn = _open_db_semantic(db_path)
        rows = _fetch_candidate_rows(
            conn,
            query_blob,
            # ARC-102: over-fetch 3x top (the same factor parsight_search
            # uses) — decay can reorder rows, so a raw-cosine LIMIT top
            # would never fetch better-decayed rows just outside it.
            top * 3,
        )
        conn.close()
    except Exception:  # noqa: BLE001 — graceful fallback
        return []

    # ARC-102: the ordering contract ("Sorted by score descending") is over
    # the DECAYED score, matching parsight_search — filter by min_score,
    # sort on the unrounded decayed score, then truncate to top.
    scored: list[tuple[float, dict[str, object]]] = []
    for stem, path, folder, title, tags_str, score, mtime in rows:
        if decay_enabled and mtime:
            score = _apply_decay(
                score, mtime, now, half_life_days=half_life, min_factor=min_factor
            )
        if score < min_score:
            continue
        tags_raw: str = tags_str if isinstance(tags_str, str) else ""
        tags: list[str] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        scored.append(
            (
                float(score),
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
                },
            )
        )

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in scored[:top]]
