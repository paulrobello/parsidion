#!/usr/bin/env python3
"""Optional par-mem code-memory backend for Parsidion.

par-mem is an external Rust code-memory system (CLI + always-on daemon, MCP
over HTTP at 127.0.0.1:4848) whose markdown indexing understands parsidion's
note conventions. This module is the availability probe and subprocess
transport for it, mirroring the ``ai_backend.py`` contract precisely:
**never raises; returns None (or False) on any failure** so callers keep
their embeddings/metadata fallback paths. Stdlib only. See docs/PAR-MEM.md.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import vault_common
import vault_config

_DEFAULT_BINARY = "par-mem"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MCP_URL = "http://127.0.0.1:4848/mcp"
_HEALTH_TIMEOUT_S = 1.0
_LOG_DIR = Path.home() / ".claude" / "logs"
_LOG_NAME = "parsidion-parmem.log"

# Per-process availability cache: str(vault) -> absolute binary path when
# available, or None when par-mem was probed and found unavailable.
_RESOLVE_CACHE: dict[str, str | None] = {}


def reset_parmem_cache() -> None:
    """Clear the per-process availability cache (test hook)."""
    _RESOLVE_CACHE.clear()


def _config_value(
    section: str, key: str, default: Any, vault: Path | None = None
) -> Any:
    """Config lookup honoring an explicit vault (mirrors ai_backend)."""
    config = vault_config.load_config(vault=vault)
    section_dict = config.get(section)
    if isinstance(section_dict, dict) and key in section_dict:
        return section_dict[key]
    return default


def _timeout_s(vault: Path | None) -> float:
    """Return the per-query subprocess timeout (``par_mem.timeout_s``, default 10)."""
    value = _config_value("par_mem", "timeout_s", _DEFAULT_TIMEOUT_S, vault=vault)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULT_TIMEOUT_S
    return float(value)


def _health_url() -> str:
    """Derive the daemon health URL from PARMEM_MCP_URL (default port 4848).

    The par-mem CLI reads the same variable to locate the daemon, so probe
    and transport always agree on the endpoint.
    """
    raw = os.environ.get("PARMEM_MCP_URL", "").strip() or _DEFAULT_MCP_URL
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        parsed = urllib.parse.urlparse(_DEFAULT_MCP_URL)
    return f"{parsed.scheme}://{parsed.netloc}/health"


def _health_ok() -> bool:
    """Return True when the par-mem daemon answers GET /health (~1s budget)."""
    try:
        with urllib.request.urlopen(_health_url(), timeout=_HEALTH_TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def _resolve_binary(vault: Path | None = None) -> str | None:
    """Return the absolute par-mem binary path, or None when unavailable.

    Availability = config gate (``par_mem.enabled``, default true) AND
    ``shutil.which(par_mem.binary)`` AND a live daemon ``/health``. The
    verdict is cached per process per vault; ``reset_parmem_cache()`` clears.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        key = str(vault)
        if key in _RESOLVE_CACHE:
            return _RESOLVE_CACHE[key]
        resolved: str | None = None
        if _config_value("par_mem", "enabled", True, vault=vault) is True:
            binary = _config_value("par_mem", "binary", _DEFAULT_BINARY, vault=vault)
            if isinstance(binary, str) and binary.strip():
                which = shutil.which(binary.strip())
                if which and _health_ok():
                    resolved = which
        _RESOLVE_CACHE[key] = resolved
        return resolved
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def resolve_parmem_backend(vault: Path | None = None) -> bool:
    """Availability probe: config gate + binary on PATH + daemon /health.

    Never raises; the result is cached per process (one ``which`` + one ~1 s
    health check), so an absent par-mem costs nothing after the first call.
    """
    return _resolve_binary(vault) is not None


# Generated vault index files that are indexed by par-mem but are not notes.
_GENERATED_NOTE_NAMES = frozenset({"CLAUDE.md", "TAGS.md", "MANIFEST.md"})


def _log_event(vault: Path, action: str, detail: str, started: float) -> None:
    """Best-effort failure log via write_hook_event; never raises.

    Entries land in ``<vault>/hook_events.log`` with ``hook="ParMemBackend"``
    so `vault-stats --hooks N` surfaces backend failures.
    """
    try:
        vault_common.write_hook_event(
            hook="ParMemBackend",
            project=vault.name,
            duration_ms=(time.monotonic() - started) * 1000,
            vault=vault,
            action=action,
            detail=detail,
        )
    except Exception:  # noqa: BLE001 — logging must never raise
        pass


def _run_parmem(
    cli_args: list[str],
    *,
    cwd: Path,
    timeout: float,
    vault: Path | None,
) -> subprocess.CompletedProcess[str] | None:
    """Run the par-mem CLI; None on launch failure or timeout.

    Uses a new session + process-group kill on timeout (the ai_backend
    discipline) so a hung daemon proxy can never orphan children.
    """
    binary = _resolve_binary(vault)
    if binary is None:
        return None
    cmd = [binary, *cli_args]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd),
            env=vault_common.env_without_claudecode(vault=vault),
            start_new_session=True,
        )
    except OSError:
        return None
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            pgid = proc.pid
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=5)
                break
            except subprocess.TimeoutExpired:
                continue
        return None
    except Exception:  # noqa: BLE001 — contract: never raises
        return None
    return subprocess.CompletedProcess(
        cmd,
        proc.returncode if proc.returncode is not None else 0,
        stdout=stdout,
        stderr=stderr,
    )


def find_code_raw(
    query: str,
    top_k: int = 10,
    cwd: Path | None = None,
    timeout: float | None = None,
    vault: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Run ``par-mem find-code <query> --json --limit <top_k>`` with cwd=*cwd*.

    Returns the MCP find_code ``results`` array verbatim (items carry a
    repo-relative ``file_path`` and an RRF ``score`` that may be null), or
    None on any failure: backend unavailable, launch failure, timeout,
    nonzero exit, or unparseable output. Failures (other than plain
    unavailability) are logged via ``write_hook_event``. Never raises.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        cwd = cwd or vault
        if not query.strip():
            return None
        if _resolve_binary(vault) is None:
            return None
        eff_timeout = float(timeout) if timeout is not None else _timeout_s(vault)
        started = time.monotonic()
        result = _run_parmem(
            ["find-code", query, "--json", "--limit", str(top_k)],
            cwd=cwd,
            timeout=eff_timeout,
            vault=vault,
        )
        if result is None:
            _log_event(vault, "find-code", "spawn-or-timeout", started)
            return None
        if result.returncode != 0:
            _log_event(vault, "find-code", f"exit:{result.returncode}", started)
            return None
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            _log_event(vault, "find-code", "bad-json", started)
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            _log_event(vault, "find-code", "missing-results", started)
            return None
        return [hit for hit in results if isinstance(hit, dict)]
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def _load_note_index_rows(
    stems: list[str], vault: Path
) -> dict[str, dict[str, Any]] | None:
    """Fetch note_index rows for *stems*; None when the DB/table is absent.

    SECURITY: the IN clause is built only from ``?`` placeholders — every
    value is a bound parameter; no identifiers derive from external input.
    """
    if not stems:
        return {}
    db_path = vault_common.get_embeddings_db_path(vault)
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return None
    try:
        conn.row_factory = sqlite3.Row
        if (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_index'"
            ).fetchone()
            is None
        ):
            return None
        placeholders = ",".join("?" for _ in stems)
        rows = conn.execute(
            f"SELECT stem, path, folder, title, summary, tags, note_type, project, "
            f"confidence, mtime, related, is_stale, incoming_links "
            f"FROM note_index WHERE stem IN ({placeholders})",
            list(stems),
        ).fetchall()
        return {str(row["stem"]): dict(row) for row in rows}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _decayed_score(score: float, mtime: object, vault: Path) -> float:
    """Apply parsidion's temporal decay when enabled and mtime is known.

    Reuses ``vault_search._apply_decay`` (lazy import: vault_search imports
    this module at its top level, so a top-level import here would cycle).
    """
    if _config_value("embeddings", "decay_enabled", True, vault=vault) is not True:
        return score
    if not isinstance(mtime, (int, float)) or not mtime:
        return score
    import vault_search  # lazy — see docstring

    return vault_search._apply_decay(score, float(mtime), time.time())


def _result_from_index_row(row: dict[str, Any], score: float) -> dict[str, object]:
    """Build an embeddings-shape result dict from a note_index row."""
    tags_str = str(row.get("tags") or "")
    related_str = str(row.get("related") or "")
    return {
        "score": round(float(score), 4),
        "stem": row.get("stem", ""),
        "title": row.get("title", ""),
        "folder": row.get("folder", ""),
        "tags": [t.strip() for t in tags_str.split(",") if t.strip()],
        "path": row.get("path", ""),
        "summary": row.get("summary", ""),
        "note_type": row.get("note_type", ""),
        "project": row.get("project", ""),
        "confidence": row.get("confidence", ""),
        "mtime": row.get("mtime"),
        "related": [r.strip() for r in related_str.split(",") if r.strip()],
        "is_stale": bool(row.get("is_stale", 0)),
        "incoming_links": row.get("incoming_links", 0),
    }


def _result_from_path(
    note_path: Path, vault: Path, score: float, mtime: float | None
) -> dict[str, object]:
    """Build a minimal embeddings-shape result dict straight from a file.

    Used only when note_index is absent (embeddings-disabled vault); mirrors
    vault_search._get_all_notes_as_results' file-walk fallback shape.
    """
    folder = note_path.parent.name if note_path.parent != vault else ""
    return {
        "score": round(float(score), 4),
        "stem": note_path.stem,
        "title": note_path.stem.replace("-", " ").title(),
        "folder": folder,
        "tags": [],
        "path": str(note_path),
        "summary": "",
        "note_type": "",
        "project": "",
        "confidence": "",
        "mtime": mtime,
        "related": [],
        "is_stale": False,
        "incoming_links": 0,
    }


def parmem_search(
    query: str,
    top_k: int = 10,
    vault: Path | None = None,
    timeout: float | None = None,
) -> list[dict[str, object]] | None:
    """Vault semantic search served by par-mem's hybrid retrieval.

    Runs ``find-code`` over the indexed vault (over-fetching 3x *top_k*
    because par-mem returns heading-section hits, several per note),
    aggregates to one row per note (max RRF score; a null score ranks
    lowest, as 0.0), enriches metadata from note_index (no extra
    round-trips), applies parsidion's temporal decay, and returns dicts
    shaped exactly like vault_search.search()'s embeddings results.
    ``min_score`` deliberately does NOT apply — RRF scores are rank-fusion
    values, not cosines. Returns None on any failure so the caller falls
    back to embeddings. Never raises.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        hits = find_code_raw(
            query, top_k=top_k * 3, cwd=vault, timeout=timeout, vault=vault
        )
        if hits is None:
            return None
        best: dict[str, tuple[float, str]] = {}
        for hit in hits:
            rel = hit.get("file_path")
            raw_score = hit.get("score")
            if not isinstance(rel, str) or not rel.lower().endswith(".md"):
                continue
            if raw_score is None:
                score = 0.0  # verified contract: null score ranks lowest
            elif isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                continue
            else:
                score = float(raw_score)
            stem = Path(rel).stem
            prev = best.get(stem)
            if prev is None or score > prev[0]:
                best[stem] = (score, rel)
        if not best:
            return []
        index_rows = _load_note_index_rows(list(best.keys()), vault)
        scored: list[tuple[float, dict[str, object]]] = []
        for stem, (raw_score, rel) in best.items():
            if index_rows is not None:
                row = index_rows.get(stem)
                if row is None:
                    continue  # not a vault note (MANIFEST.md, TAGS.md, ...)
                final = _decayed_score(raw_score, row.get("mtime"), vault)
                scored.append((final, _result_from_index_row(row, final)))
            else:
                note_path = vault / rel
                if note_path.name in _GENERATED_NOTE_NAMES or not note_path.is_file():
                    continue
                try:
                    file_mtime: float | None = note_path.stat().st_mtime
                except OSError:
                    file_mtime = None
                final = _decayed_score(raw_score, file_mtime, vault)
                scored.append(
                    (final, _result_from_path(note_path, vault, final, file_mtime))
                )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def spawn_background_index(vault: Path | None = None) -> bool:
    """Launch a detached background ``par-mem index <vault> --json``.

    NDJSON progress is appended to ``~/.claude/logs/parsidion-parmem.log``
    (the same detach pattern update_index.py uses for build_embeddings.py).
    Returns True when the process launched; never blocks, never raises.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        binary = _resolve_binary(vault)
        if binary is None:
            return False
        _LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = open(_LOG_DIR / _LOG_NAME, "a", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [binary, "index", str(vault), "--json"],
            stdout=log,
            stderr=log,
            env=vault_common.env_without_claudecode(vault=vault),
            start_new_session=True,
        )
        return True
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def _spawn_watch_command(verb: str, vault: Path | None, session_id: str) -> bool:
    """Fire-and-forget ``par-mem <verb> <vault> --hold-token parsidion-<id>``.

    Detached Popen (never blocks the calling hook); stdout/stderr append to
    ``~/.claude/logs/parsidion-parmem.log``. The daemon refcounts holds and
    expires them by TTL server-side, so a crashed session cannot leak one.
    Never raises.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        token = session_id.strip()
        if not token:
            return False
        binary = _resolve_binary(vault)
        if binary is None:
            return False
        _LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = open(_LOG_DIR / _LOG_NAME, "a", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [binary, verb, str(vault), "--hold-token", f"parsidion-{token}"],
            stdout=log,
            stderr=log,
            env=vault_common.env_without_claudecode(vault=vault),
            start_new_session=True,
        )
        return True
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def spawn_watch(vault: Path | None, session_id: str) -> bool:
    """Hold a par-mem live-reindex watch on the vault for this session."""
    return _spawn_watch_command("watch", vault, session_id)


def spawn_unwatch(vault: Path | None, session_id: str) -> bool:
    """Release this session's par-mem watch hold on the vault."""
    return _spawn_watch_command("unwatch", vault, session_id)


def _vault_repo_state(payload: object, vault: Path) -> str:
    """Classify *vault* in a verbatim `par-mem repos --json` payload.

    Returns ``"fresh"``, ``"stale"``, ``"absent"``, or ``"invalid"``
    (unparseable payload). The vault matches a repo by canonicalized
    ``root_path`` or any worktree ``path`` (``os.path.realpath`` both
    sides). Freshness comes from the matched worktree's ``stale`` flag; a
    root_path match with no matching worktree row falls back to the primary
    worktree's flag, and counts as fresh when no worktree row is readable
    (no staleness signal).
    """
    if not isinstance(payload, dict):
        return "invalid"
    repos = payload.get("repositories")
    if not isinstance(repos, list):
        return "invalid"
    vault_real = os.path.realpath(str(vault))
    for repo in repos:
        if not isinstance(repo, dict):
            continue
        worktrees = repo.get("worktrees")
        wt_rows: list[dict[str, Any]] = (
            [w for w in worktrees if isinstance(w, dict)]
            if isinstance(worktrees, list)
            else []
        )
        root = repo.get("root_path")
        repo_matches = isinstance(root, str) and os.path.realpath(root) == vault_real
        matched_wt: dict[str, Any] | None = None
        for wt in wt_rows:
            wt_path = wt.get("path")
            if isinstance(wt_path, str) and os.path.realpath(wt_path) == vault_real:
                matched_wt = wt
                break
        if matched_wt is None and repo_matches:
            for wt in wt_rows:
                if wt.get("is_primary"):
                    matched_wt = wt
                    break
        if matched_wt is not None:
            return "stale" if bool(matched_wt.get("stale", False)) else "fresh"
        if repo_matches:
            return "fresh"
    return "absent"


def ensure_vault_indexed(vault: Path | None = None) -> bool:
    """Return True when the current query may be served by par-mem.

    Freshness comes from ``par-mem repos --json`` (the verbatim
    list_indexed_repositories result; proxy-only — exit 2 without a daemon):

    - **fresh** → True.
    - **stale** → kick a detached background ``par-mem index`` and STILL
      return True: a stale index is usable, so this query serves from it
      while the reindex catches up.
    - **absent** → kick a background index and return False so the CURRENT
      query falls back to embeddings (a later query picks par-mem up).
    - **failed/garbled ``repos``** → False WITHOUT spawning — never reindex
      blind when the daemon cannot even list its repositories.

    Never raises.
    """
    try:
        vault = vault or vault_common.resolve_vault()
        if _resolve_binary(vault) is None:
            return False
        started = time.monotonic()
        result = _run_parmem(
            ["repos", "--json"], cwd=vault, timeout=_timeout_s(vault), vault=vault
        )
        if result is None or result.returncode != 0:
            _log_event(vault, "repos", "spawn-timeout-or-nonzero", started)
            return False
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            _log_event(vault, "repos", "bad-json", started)
            return False
        state = _vault_repo_state(payload, vault)
        if state == "fresh":
            return True
        if state == "stale":
            spawn_background_index(vault)
            return True
        if state == "absent":
            spawn_background_index(vault)
            return False
        _log_event(vault, "repos", "unexpected-shape", started)
        return False
    except Exception:  # noqa: BLE001 — contract: never raises
        return False
