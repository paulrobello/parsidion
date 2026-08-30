#!/usr/bin/env python3
"""Optional parsight code-memory backend for Parsidion.

parsight is an external Rust code-memory system (CLI + always-on daemon, MCP
over HTTP at 127.0.0.1:4848) whose markdown indexing understands parsidion's
note conventions. This module is the availability probe and subprocess
transport for it, mirroring the ``ai_backend.py`` contract precisely:
**never raises; returns None (or False) on any failure** so callers keep
their embeddings/metadata fallback paths. Stdlib only. See docs/PARSIGHT.md.
ARC-006: lives in the ``core/`` package; the flat ``parsight_backend.py`` name
at the scripts root remains as a re-export shim for existing importers.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from . import vault_config, vault_fs
from .subproc_util import run_with_pgkill
from .vault_config import apply_decay_score, resolve_decay_params
from .vault_hooks import write_hook_event
from .vault_path import get_embeddings_db_path, is_path_inside_vault, resolve_vault

__all__: list[str] = [
    "daemon_watches_vault",
    "doc_links_raw",
    "ensure_vault_indexed",
    "find_code_raw",
    "parsight_search",
    "reset_parsight_cache",
    "resolve_parsight_backend",
    "spawn_background_index",
    "spawn_unwatch",
    "spawn_watch",
    "vault_index_fresh",
]

_DEFAULT_BINARY = "parsight"
# Pre-rename binary name (compat): a deployed machine may expose only the
# legacy command on PATH, so default resolution falls back to it.
_LEGACY_BINARY = "par-mem"
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MCP_URL = "http://127.0.0.1:4848/mcp"
_HEALTH_TIMEOUT_S = 1.0
# Per-request budget for the one-shot MCP watch-coverage probe (two POSTs on
# loopback; generous ceiling so a busy daemon is not mistaken for an absent one).
_MCP_PROBE_TIMEOUT_S = 2.0
# Streamable-HTTP MCP protocol version the probe speaks (echoed by the daemon).
_MCP_PROTOCOL_VERSION = "2025-06-18"
_LOG_DIR = Path.home() / ".claude" / "logs"
_LOG_NAME = "parsidion-parsight.log"

# SEC-206: parsight subprocesses talk to a local daemon and never need
# Anthropic credentials — build their env from this minimal allowlist
# (pattern: ``_CODEX_ENV_KEYS``/``_GROK_ENV_KEYS`` in core/ai_backend.py)
# instead of forwarding every ``_SAFE_ENV_KEYS`` entry. ``PARSIGHT_MCP_URL``
# overrides the daemon endpoint; PATH/HOME cover binary resolution and
# config discovery. Add a key only after verifying the CLI reads it.
_PARSIGHT_ENV_KEYS = ("PATH", "HOME", "PARSIGHT_MCP_URL")


def _parsight_env() -> dict[str, str]:
    """Return the least-privilege env for a parsight CLI subprocess (SEC-206)."""
    return {
        key: value for key, value in os.environ.items() if key in _PARSIGHT_ENV_KEYS
    }


# Per-process availability cache: str(vault) -> absolute binary path when
# available, or None when parsight was probed and found unavailable.
_RESOLVE_CACHE: dict[str, str | None] = {}


def _which_default_binary() -> str | None:
    """Resolve the default binary name: prefer parsight, fall back to par-mem."""
    return shutil.which(_DEFAULT_BINARY) or shutil.which(_LEGACY_BINARY)


def reset_parsight_cache() -> None:
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
    """Return the per-query subprocess timeout (``parsight.timeout_s``, default 10)."""
    value = _config_value("parsight", "timeout_s", _DEFAULT_TIMEOUT_S, vault=vault)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULT_TIMEOUT_S
    return float(value)


def _mcp_url() -> str:
    """Normalize the daemon MCP endpoint from PARSIGHT_MCP_URL (default 4848).

    The parsight CLI reads the same variable to locate the daemon, so every
    probe and transport in this module agrees on the endpoint.
    """
    raw = os.environ.get("PARSIGHT_MCP_URL", "").strip() or _DEFAULT_MCP_URL
    parsed = urllib.parse.urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        parsed = urllib.parse.urlparse(_DEFAULT_MCP_URL)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/mcp'}"


def _health_url() -> str:
    """Derive the daemon health URL from the normalized MCP endpoint."""
    parsed = urllib.parse.urlparse(_mcp_url())
    return f"{parsed.scheme}://{parsed.netloc}/health"


def _health_ok() -> bool:
    """Return True when the parsight daemon answers GET /health (~1s budget)."""
    try:
        with urllib.request.urlopen(_health_url(), timeout=_HEALTH_TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def _mcp_post(
    url: str, request: dict[str, Any], session_id: str | None
) -> tuple[str, str | None]:
    """POST one JSON-RPC message to the daemon's MCP endpoint.

    Returns ``(body, session_id)`` where *session_id* is the
    ``Mcp-Session-Id`` the response carried (the daemon issues one on
    ``initialize`` and rejects session-less subsequent calls). Raises on any
    transport/HTTP error — callers wanting the never-raise contract wrap in
    try/except.
    """
    data = json.dumps(request).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=_MCP_PROBE_TIMEOUT_S) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return body, resp.headers.get("Mcp-Session-Id")


def _mcp_response_payload(body: str, request_id: int) -> dict[str, Any] | None:
    """Extract the JSON-RPC payload for *request_id* from an MCP response body.

    Handles both response shapes a streamable-HTTP MCP server may emit: a
    plain JSON document, or an SSE stream whose ``data:`` lines carry the
    payload (the parsight daemon emits SSE, with keep-alive/noise lines
    around it). Returns None when no matching payload is found.
    """
    candidates: list[str] = []
    stripped = body.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    else:
        candidates.extend(
            line[len("data:") :].strip()
            for line in body.splitlines()
            if line.startswith("data:")
        )
    for chunk in candidates:
        try:
            payload = json.loads(chunk)
        except (json.JSONDecodeError, ValueError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("id") == request_id
            and ("result" in payload or "error" in payload)
        ):
            return payload
    return None


def _daemon_watches_vault(vault: Path) -> bool:
    """Return True when the daemon's watcher already covers *vault*.

    The parsight CLI has no watch-list subcommand, so this asks the daemon's
    MCP endpoint directly (the same endpoint the health probe uses): one
    ``initialize`` handshake POST capturing the session id, then one
    ``tools/call list_watched_paths`` POST. Watched paths compare by
    ``os.path.realpath`` on both sides, mirroring ``_vault_repo_state``.

    False on ANY failure (endpoint absent, protocol mismatch, garbage
    payload): unknown coverage must not suppress the manual index. Never
    raises.
    """
    try:
        url = _mcp_url()
        init_body, session_id = _mcp_post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "parsidion-parsight-backend",
                        "version": "1",
                    },
                },
            },
            None,
        )
        if _mcp_response_payload(init_body, 1) is None:
            return False
        call_body, _ = _mcp_post(
            url,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_watched_paths", "arguments": {}},
            },
            session_id,
        )
        payload = _mcp_response_payload(call_body, 2)
        if not isinstance(payload, dict) or "error" in payload:
            return False
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            return False
        text: str | None = None
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ):
                    text = item["text"]
                    break
        if text is None:
            return False
        watched = json.loads(text)
        paths = watched.get("watched_paths") if isinstance(watched, dict) else None
        if not isinstance(paths, list):
            return False
        vault_real = os.path.realpath(str(vault))
        return any(
            isinstance(p, str) and os.path.realpath(p) == vault_real for p in paths
        )
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def daemon_watches_vault(vault: Path | None = None) -> bool:
    """Public gate for callers deciding whether to spawn a manual index.

    Same probe and fail-open contract as :func:`_daemon_watches_vault`
    (False on any failure — unknown coverage never suppresses a manual
    index); exported so update_index.py's end-of-run trigger can skip its
    spawn when the daemon's watcher already re-indexes the vault on every
    note write.
    """
    if vault is None:
        vault = resolve_vault()
    return _daemon_watches_vault(vault)


def _resolve_binary(vault: Path | None = None) -> str | None:
    """Return the absolute parsight binary path, or None when unavailable.

    Availability = config gate (``parsight.enabled``, default true) AND a
    resolvable binary AND a live daemon ``/health``. Binary resolution: an
    explicitly configured ``parsight.binary`` is used as-is (bare name or
    path, PATH-resolved); when unset (or set to the default name) resolution
    prefers ``parsight`` and falls back to the legacy ``par-mem`` command
    when only that is on PATH. The verdict is cached per process per vault;
    ``reset_parsight_cache()`` clears.
    """
    try:
        vault = vault or resolve_vault()
        key = str(vault)
        if key in _RESOLVE_CACHE:
            return _RESOLVE_CACHE[key]
        resolved: str | None = None
        if _config_value("parsight", "enabled", True, vault=vault) is True:
            binary = _config_value("parsight", "binary", _DEFAULT_BINARY, vault=vault)
            which: str | None = None
            raw = binary.strip() if isinstance(binary, str) else ""
            if raw and raw != _DEFAULT_BINARY:
                which = shutil.which(raw)
                # SEC-007: a path-like parsight.binary from a synced
                # config.yaml can point at an attacker-writable script; it
                # must pass the ownership/write-bits gate. Bare names (and
                # the default) stay on plain PATH resolution. On refusal,
                # fall back to the default command name.
                if (
                    which
                    and ("/" in raw or os.path.isabs(raw))
                    and not vault_fs.is_trusted_executable(which)
                ):
                    fallback = _which_default_binary()
                    print(
                        f"parsight_backend: parsight.binary {raw!r} failed the "
                        f"trust check (not owned by the current user or "
                        f"group/world-writable); using "
                        f"{fallback!r} instead. SEC-007",
                        file=sys.stderr,
                    )
                    which = fallback
            else:
                which = _which_default_binary()
            if which and _health_ok():
                resolved = which
        _RESOLVE_CACHE[key] = resolved
        return resolved
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def resolve_parsight_backend(vault: Path | None = None) -> bool:
    """Availability probe: config gate + binary on PATH + daemon /health.

    Never raises; the result is cached per process (one ``which`` + one ~1 s
    health check), so an absent parsight costs nothing after the first call.
    """
    return _resolve_binary(vault) is not None


# Generated vault index files that are indexed by parsight but are not notes.
_GENERATED_NOTE_NAMES = frozenset({"CLAUDE.md", "TAGS.md", "MANIFEST.md"})


def _log_event(vault: Path, action: str, detail: str, started: float) -> None:
    """Best-effort failure log via write_hook_event; never raises.

    Entries land in ``<vault>/hook_events.log`` with ``hook="ParsightBackend"``
    so `vault-stats --hooks N` surfaces backend failures.
    """
    try:
        write_hook_event(
            hook="ParsightBackend",
            project=vault.name,
            duration_ms=(time.monotonic() - started) * 1000,
            vault=vault,
            action=action,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — logging must never raise
        print(f"parsight hook event log failed: {exc}", file=sys.stderr)
        pass


def _run_parsight(
    cli_args: list[str],
    *,
    cwd: Path,
    timeout: float,
    vault: Path | None,
    kill_grace_secs: float | None = None,
) -> tuple[str, subprocess.CompletedProcess[str] | None]:
    """Run the parsight CLI; returns ``(reason, proc)``.

    ``reason`` is ``"ok"`` on normal completion (``proc`` set, any
    returncode), ``"launch"`` when the binary could not be started, or
    ``"timeout"`` when it exceeded *timeout* seconds. ``proc`` is None
    whenever ``reason`` isn't ``"ok"``; there is no captured stderr for
    those cases (the process never finished), so callers logging failure
    detail should only look at ``proc.stderr`` when ``proc`` is not None.

    SEC-122 / ARC-048f: delegates to ``subproc_util.run_with_pgkill`` —
    the shared process-group-kill implementation extracted from this and
    ``ai_backend._run_prompt_subprocess`` (which had drifted). The 3a wave
    should repoint ai_backend at the same helper.

    SEC-206: the child env is the ``_PARSIGHT_ENV_KEYS`` allowlist, not
    ``env_without_claudecode`` — the parsight CLI never needs the
    Anthropic credentials that helper forwards.
    """
    binary = _resolve_binary(vault)
    if binary is None:
        return "launch", None
    cmd = [binary, *cli_args]
    return run_with_pgkill(
        cmd,
        cwd=cwd,
        timeout=timeout,
        env=_parsight_env(),
        kill_grace_secs=kill_grace_secs,
    )


def _sanitize_detail(reason: str, stderr: str) -> str:
    """Build a single-line log detail: *reason* plus a short stderr excerpt.

    Newlines/whitespace in *stderr* are collapsed so the ``write_hook_event``
    line stays single-line, then truncated to 200 chars. Pure string
    manipulation on values that are always ``str`` — cannot raise.
    """
    excerpt = " ".join(stderr.split())[:200]
    return f"{reason} stderr={excerpt}" if excerpt else reason


def find_code_raw(
    query: str,
    top_k: int = 10,
    cwd: Path | None = None,
    timeout: float | None = None,
    vault: Path | None = None,
    kill_grace_secs: float | None = None,
) -> list[dict[str, Any]] | None:
    """Run ``parsight find-code <query> --json --diagnostics --limit <top_k>``.

    ``--diagnostics`` asks the daemon for per-result RRF scores (without it,
    ``score`` is null on every result). An older parsight binary that
    predates the flag rejects it and exits nonzero, which falls through the
    existing failure path below (logged, returns None) — the documented
    graceful degradation.

    Returns the MCP find_code ``results`` array verbatim (items carry a
    repo-relative ``file_path`` and an RRF ``score`` that may be null), or
    None on any failure: backend unavailable, launch failure, timeout,
    nonzero exit, or unparseable output. Failures (other than plain
    unavailability) are logged via ``write_hook_event`` with a reason tag
    (``"launch"``/``"timeout"``/``"exit:N"``/``"bad-json"``/``"missing-results"``)
    plus a sanitized stderr excerpt when the process completed. Never raises.
    """
    try:
        vault = vault or resolve_vault()
        cwd = cwd or vault
        if not query.strip():
            return None
        if _resolve_binary(vault) is None:
            return None
        eff_timeout = float(timeout) if timeout is not None else _timeout_s(vault)
        started = time.monotonic()
        reason, result = _run_parsight(
            ["find-code", query, "--json", "--diagnostics", "--limit", str(top_k)],
            cwd=cwd,
            timeout=eff_timeout,
            vault=vault,
            kill_grace_secs=kill_grace_secs,
        )
        if result is None:
            _log_event(vault, "find-code", reason, started)
            return None
        if result.returncode != 0:
            _log_event(
                vault,
                "find-code",
                _sanitize_detail(f"exit:{result.returncode}", result.stderr),
                started,
            )
            return None
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            _log_event(
                vault, "find-code", _sanitize_detail("bad-json", result.stderr), started
            )
            return None
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            _log_event(
                vault,
                "find-code",
                _sanitize_detail("missing-results", result.stderr),
                started,
            )
            return None
        return [hit for hit in results if isinstance(hit, dict)]
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def doc_links_raw(
    cwd: Path | None = None,
    timeout: float | None = None,
    vault: Path | None = None,
) -> list[dict[str, Any]] | None:
    """Run ``parsight doc-links --json --targets doc --limit 200000``.

    Returns the MCP ``links`` array verbatim (items carry vault-root-relative
    ``source_path``/``target_path``, ``target_is_doc``, and a section-level
    ``count``), or None on any failure: backend unavailable, launch failure,
    timeout, nonzero exit (including an older binary without the subcommand),
    or unparseable output. Failures are logged via ``write_hook_event`` with
    the module's standard reason tags. Never raises.
    """
    try:
        vault = vault or resolve_vault()
        cwd = cwd or vault
        if _resolve_binary(vault) is None:
            return None
        eff_timeout = float(timeout) if timeout is not None else _timeout_s(vault)
        started = time.monotonic()
        reason, result = _run_parsight(
            ["doc-links", "--json", "--targets", "doc", "--limit", "200000"],
            cwd=cwd,
            timeout=eff_timeout,
            vault=vault,
        )
        if result is None:
            _log_event(vault, "doc-links", reason, started)
            return None
        if result.returncode != 0:
            _log_event(
                vault,
                "doc-links",
                _sanitize_detail(f"exit:{result.returncode}", result.stderr),
                started,
            )
            return None
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            _log_event(
                vault, "doc-links", _sanitize_detail("bad-json", result.stderr), started
            )
            return None
        links = payload.get("links") if isinstance(payload, dict) else None
        if not isinstance(links, list):
            _log_event(
                vault,
                "doc-links",
                _sanitize_detail("missing-links", result.stderr),
                started,
            )
            return None
        if isinstance(payload, dict) and payload.get("truncated"):
            _log_event(vault, "doc-links", f"truncated:{payload.get('total')}", started)
        return [link for link in links if isinstance(link, dict)]
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
    db_path = get_embeddings_db_path(vault)
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


def _decayed_score(
    score: float,
    mtime: object,
    *,
    decay_enabled: bool,
    half_life: float,
    min_factor: float,
    now: float,
) -> float:
    """Apply parsidion's temporal decay when enabled and mtime is known.

    PRF-103: the decay configuration (enabled flag, half-life, min-factor,
    reference time) is resolved once per search by the caller — previously
    this helper re-read config for every scored row. ARC-023:
    ``apply_decay_score`` now lives on ``vault_config`` (a leaf module
    this file already depends on), so the previous lazy ``import vault_search``
    — required because vault_search top-level imports parsight_backend — is gone.
    The cycle is broken at the import-graph level, not by deferring the import.
    """
    if not decay_enabled:
        return score
    if not isinstance(mtime, (int, float)) or not mtime:
        return score
    return apply_decay_score(
        score, float(mtime), now, half_life_days=half_life, min_factor=min_factor
    )


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


def parsight_search(
    query: str,
    top_k: int = 10,
    vault: Path | None = None,
    timeout: float | None = None,
    kill_grace_secs: float | None = None,
) -> list[dict[str, object]] | None:
    """Vault semantic search served by parsight's hybrid retrieval.

    Runs ``find-code --diagnostics`` over the indexed vault (over-fetching
    3x *top_k*, clamped to find-code's server-side 1000 limit ceiling,
    because parsight returns heading-section hits, several per note),
    aggregates to one row per note (max score per hit — a hit's own
    RRF score when present, else a rank-preserving synthetic value derived
    from its position in parsight's response, so a score-less hit still
    contributes its relevance order through aggregation/decay/sort instead
    of collapsing to a tie), enriches metadata from note_index (no extra
    round-trips), applies parsidion's temporal decay, and returns dicts
    shaped exactly like vault_search.search()'s embeddings results.
    ``min_score`` deliberately does NOT apply — RRF scores are rank-fusion
    values, not cosines. Returns None on any failure so the caller falls
    back to embeddings. Never raises.
    """
    try:
        vault = vault or resolve_vault()
        hits = find_code_raw(
            query,
            top_k=min(top_k * 3, 1000),
            cwd=vault,
            timeout=timeout,
            vault=vault,
            kill_grace_secs=kill_grace_secs,
        )
        if hits is None:
            return None
        best: dict[str, tuple[float, str]] = {}
        for idx, hit in enumerate(hits):
            rel = hit.get("file_path")
            raw_score = hit.get("score")
            if not isinstance(rel, str) or not rel.lower().endswith(".md"):
                continue
            # SEC-020: parsight JSON is external input; a file_path outside
            # the vault (absolute or ../-laden) must be dropped before it
            # reaches the result set or a file read. `vault / rel` collapses
            # an absolute rel to itself, so the containment check holds for
            # both shapes.
            if not is_path_inside_vault(vault / rel, vault):
                continue
            if raw_score is None:
                # Rank-preserving fallback: a hit without a score (older
                # daemon predating --diagnostics, or a lane that omits one)
                # still carries relevance information in its position in
                # parsight's response — synthesize from that instead of
                # flooring to 0.0, so aggregation/decay/sort don't collapse
                # score-less hits into an arbitrary tie.
                score = 1.0 / (1.0 + idx)
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
        # PRF-103: resolve the decay configuration once per search —
        # previously _decayed_score re-read config for every scored row.
        decay_enabled = (
            _config_value("embeddings", "decay_enabled", True, vault=vault) is True
        )
        half_life, min_factor = resolve_decay_params(vault)
        now = time.time()
        scored: list[tuple[float, dict[str, object]]] = []
        for stem, (raw_score, rel) in best.items():
            if index_rows is not None:
                row = index_rows.get(stem)
                if row is None:
                    continue  # not a vault note (MANIFEST.md, TAGS.md, ...)
                # SEC-020: note_index rows carry DB-sourced path strings; a
                # tampered row must not inject an outside path into results.
                row_path = Path(str(row.get("path") or ""))
                if row_path.name and not is_path_inside_vault(row_path, vault):
                    continue
                final = _decayed_score(
                    raw_score,
                    row.get("mtime"),
                    decay_enabled=decay_enabled,
                    half_life=half_life,
                    min_factor=min_factor,
                    now=now,
                )
                scored.append((final, _result_from_index_row(row, final)))
            else:
                note_path = vault / rel
                if note_path.name in _GENERATED_NOTE_NAMES or not note_path.is_file():
                    continue
                try:
                    file_mtime: float | None = note_path.stat().st_mtime
                except OSError:
                    file_mtime = None
                final = _decayed_score(
                    raw_score,
                    file_mtime,
                    decay_enabled=decay_enabled,
                    half_life=half_life,
                    min_factor=min_factor,
                    now=now,
                )
                scored.append(
                    (final, _result_from_path(note_path, vault, final, file_mtime))
                )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [entry for _, entry in scored[:top_k]]
    except Exception:  # noqa: BLE001 — contract: never raises
        return None


def spawn_background_index(vault: Path | None = None) -> bool:
    """Launch a detached background ``parsight index <vault> --json --no-wait``.

    NDJSON progress is appended to ``~/.claude/logs/parsidion-parsight.log``
    (the same detach pattern update_index.py uses for build_embeddings.py).
    ``--no-wait`` makes the CLI submit the job and exit immediately — nobody
    reads the result event or the Popen handle, so a wait could only leave
    the detached CLI polling a daemon job that queues behind the daemon's
    own watcher on the same vault (the orphaned-stuck-process class fixed
    here; cross-repo parsight card 019fe747 bounded the hang on its side).
    Returns True when the process launched; never blocks the calling hook
    aside from the accepted, cached ≤1s availability probe — the subprocess
    launch itself never blocks. Never raises.
    """
    try:
        vault = vault or resolve_vault()
        binary = _resolve_binary(vault)
        if binary is None:
            return False
        _LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = open(_LOG_DIR / _LOG_NAME, "a", encoding="utf-8")  # noqa: SIM115
        subprocess.Popen(
            [binary, "index", str(vault), "--json", "--no-wait"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=_parsight_env(),
            start_new_session=True,
        )
        return True
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def _spawn_watch_command(verb: str, vault: Path | None, session_id: str) -> bool:
    """Fire-and-forget ``parsight <verb> <vault> --hold-token parsidion-<id>``.

    Detached Popen — never blocks the calling hook aside from the accepted,
    cached ≤1s availability probe; the subprocess launch itself never
    blocks. stdout/stderr append to ``~/.claude/logs/parsidion-parsight.log``.
    The daemon refcounts holds and expires them by TTL server-side, so a
    crashed session cannot leak one. Never raises.
    """
    try:
        vault = vault or resolve_vault()
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
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=_parsight_env(),
            start_new_session=True,
        )
        return True
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def spawn_watch(vault: Path | None, session_id: str) -> bool:
    """Hold a parsight live-reindex watch on the vault for this session."""
    return _spawn_watch_command("watch", vault, session_id)


def spawn_unwatch(vault: Path | None, session_id: str) -> bool:
    """Release this session's parsight watch hold on the vault."""
    return _spawn_watch_command("unwatch", vault, session_id)


def _vault_repo_state(payload: object, vault: Path) -> str:
    """Classify *vault* in a verbatim `parsight repos --json` payload.

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


def _repos_state(vault: Path) -> str:
    """Fetch ``parsight repos --json`` and classify *vault*.

    Returns ``"unavailable"`` (parsight off / binary missing / daemon down /
    launch-timeout / nonzero exit), ``"invalid"`` (unparseable or unexpected
    payload), or the :func:`_vault_repo_state` verdict (``"fresh"`` /
    ``"stale"`` / ``"absent"``). Failures other than plain unavailability are
    logged with the module's ``repos`` reason tags. Never raises.
    """
    if _resolve_binary(vault) is None:
        return "unavailable"
    started = time.monotonic()
    _, result = _run_parsight(
        ["repos", "--json"], cwd=vault, timeout=_timeout_s(vault), vault=vault
    )
    if result is None or result.returncode != 0:
        _log_event(vault, "repos", "spawn-timeout-or-nonzero", started)
        return "unavailable"
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        _log_event(vault, "repos", "bad-json", started)
        return "invalid"
    state = _vault_repo_state(payload, vault)
    if state == "invalid":
        _log_event(vault, "repos", "unexpected-shape", started)
    return state


def ensure_vault_indexed(vault: Path | None = None) -> bool:
    """Return True when the current query may be served by parsight.

    Freshness comes from :func:`_repos_state` (``parsight repos --json``, the
    verbatim list_indexed_repositories result; proxy-only — exit 2 without a
    daemon):

    - **fresh** → True.
    - **stale** → STILL return True (a stale index is usable, so this query
      serves from it while it catches up). A background ``parsight index``
      is kicked only when the daemon's watcher does NOT already cover the
      vault (:func:`_daemon_watches_vault`): the watcher re-indexes on the
      very file changes/commits that made the index stale, so a manual job
      would only queue behind it and contend for the index writer. An
      unwatched stale vault keeps the manual kick — nothing else would
      catch it up.
    - **absent** → kick a background index and return False so the CURRENT
      query falls back to embeddings (a later query picks parsight up).
      This fires even when the vault is watched: a watch only reacts to
      file changes, it never performs a never-indexed repo's initial index.
    - **unavailable/invalid** → False WITHOUT spawning — never reindex
      blind when the daemon cannot even list its repositories.

    Never raises.
    """
    try:
        vault = vault or resolve_vault()
        state = _repos_state(vault)
        if state == "fresh":
            return True
        if state == "stale":
            if _daemon_watches_vault(vault):
                return True
            spawn_background_index(vault)
            return True
        if state == "absent":
            spawn_background_index(vault)
            return False
        return False  # unavailable or invalid — never spawn blind
    except Exception:  # noqa: BLE001 — contract: never raises
        return False


def vault_index_fresh(vault: Path | None = None) -> tuple[bool, str]:
    """Return ``(is_fresh, reason)`` for deterministic-build callers.

    Only ``(True, "fresh")`` authorizes trusting parsight's body-link
    enrichment; every other state returns ``(False, reason)`` so the caller
    skips enrichment instead of emitting partial, run-to-run-variable edges
    from a stale or mid-catch-up index. ``reason`` is the :func:`_repos_state`
    verdict (``fresh`` / ``stale`` / ``absent`` / ``invalid`` /
    ``unavailable``). Side-effect-free — unlike :func:`ensure_vault_indexed`,
    this never spawns a background reindex (the build path must not mutate
    index state). Never raises.
    """
    try:
        vault = vault or resolve_vault()
        state = _repos_state(vault)
        return (state == "fresh", state)
    except Exception:  # noqa: BLE001 — contract: never raises
        return (False, "error")
