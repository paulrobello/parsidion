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

import os
import shutil
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
