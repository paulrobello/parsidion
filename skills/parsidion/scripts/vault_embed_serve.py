#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
# ]
# ///
"""Persistent embedding service for Parsidion (ENH-003, opt-in).

A long-lived process that loads the fastembed ONNX model once and serves embed
requests over a Unix domain socket, so separate ``vault_search`` callers (each
its own short-lived process) share one warm model instead of each paying a
~67 MB cold load.

OPT-IN. ``vault_search`` only contacts/starts this service when
``embeddings.service_enabled`` is true AND the active search backend is not
par-mem — par-mem serves retrieval without local query embeddings, so the
service is pointless (and never reached) under it. When disabled (the default)
or under par-mem, ``vault_search`` uses its in-process cached model instead.

Protocol: newline-delimited JSON over AF_UNIX, one request/response per line::

    request:  {"text": "...", "model": "BAAI/bge-small-en-v1.5"}
    response: {"vector": [0.0123, ...]}   |   {"error": "..."}

Lifecycle: launched detached by the first enabled client; idle-exits after
``--idle-exit`` seconds with no connection (default 600). A PID file guards
against double-launch; a stale PID (dead process) is reclaimed.

Usage::

    uv run vault_embed_serve.py --vault ~/ParsidionVault --model BAAI/bge-small-en-v1.5

Security: AF_UNIX only (never TCP — the repo threat model treats network
services as a surface, SEC-102). Socket + PID live OUTSIDE the synced vault
tree (under ``~/.claude/parsidion-embed/``) at mode 0600, so multi-machine
vault git sync never transports machine-local IPC artifacts.

The path helpers below (``socket_path`` / ``pid_path`` / ``runtime_dir``) are
imported by ``vault_search`` so client and server agree on the socket location
without a handshake; they are stdlib-only and have no import side effects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any

_DEFAULT_IDLE_EXIT = 600
_RUNTIME_DIR = Path.home() / ".claude" / "parsidion-embed"


# ---------------------------------------------------------------------------
# Path helpers (shared with vault_search — keep this formula in sync there)
# ---------------------------------------------------------------------------


def _vault_hash(vault: Path) -> str:
    """Stable short id for a vault path (used to name its socket/pid files)."""
    return hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]


def runtime_dir() -> Path:
    """Per-user runtime dir for embed-service IPC artifacts (mode 0700)."""
    d = _RUNTIME_DIR
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def socket_path(vault: Path) -> Path:
    """AF_UNIX socket path for *vault*'s embed service."""
    return runtime_dir() / f"embed-{_vault_hash(vault)}.sock"


def pid_path(vault: Path) -> Path:
    """PID-file path for *vault*'s embed service (singleton guard)."""
    return runtime_dir() / f"embed-{_vault_hash(vault)}.pid"


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _write_pid(path: Path, pid: int) -> None:
    path.write_text(str(pid), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    """True when *pid* is a running process (best-effort, never raises)."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

_MODELS: dict[str, Any] = {}


def _get_model(model_name: str) -> Any:
    """Return a cached ``TextEmbedding`` for *model_name* (loaded once)."""
    m = _MODELS.get(model_name)
    if m is None:
        from fastembed import TextEmbedding  # type: ignore[import-untyped]

        m = TextEmbedding(model_name=model_name)
        _MODELS[model_name] = m
    return m


# SEC-018: request-line cap. A client that never sends a newline would
# otherwise grow the buffer without bound (the AF_UNIX socket is local-only,
# but the service is user-started and shareable across processes).
_MAX_REQUEST_BYTES = 64 * 1024


def _read_line(conn: socket.socket) -> str:
    """Read one newline-terminated request line from *conn*.

    SEC-018: stops at 64 KiB and closes the connection — a request that
    large is malformed, and an unbounded read is a trivial memory-exhaustion
    lever for any local client.
    """
    buf = bytearray()
    while True:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if b"\n" in chunk:
            break
        if len(buf) > _MAX_REQUEST_BYTES:
            raise ValueError("request line exceeds 64 KiB limit")
    return buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()


def _handle(conn: socket.socket, default_model: str) -> None:
    """Serve one embed request; respond with a vector or an error object."""
    conn.settimeout(60.0)
    try:
        line = _read_line(conn)
        if not line:
            return
        req = json.loads(line)
        text = str(req.get("text", ""))
        # SEC-018: the model comes from server config only. A client-chosen
        # model name would let any local process load an arbitrary
        # (potentially huge or hostile-source) ONNX bundle into the daemon.
        model_name = default_model
        model = _get_model(model_name)
        vec = list(model.embed([text]))[0]
        payload = json.dumps({"vector": [float(x) for x in vec]}) + "\n"
        conn.sendall(payload.encode("utf-8"))
    except Exception as e:  # noqa: BLE001 — report any failure to the client
        try:
            conn.sendall((json.dumps({"error": str(e)[:200]}) + "\n").encode("utf-8"))
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def _cleanup(sock_path: Path, pid_file: Path, our_pid: int) -> None:
    """Remove only our own socket/pid files (never a successor daemon's)."""
    if _read_pid(pid_file) == our_pid:
        try:
            pid_file.unlink()
        except OSError:
            pass
    try:
        sock_path.unlink()
    except OSError:
        pass


def main() -> int:
    """Parse CLI args and serve embed requests until idle-exit or shutdown."""
    parser = argparse.ArgumentParser(
        description="Parsidion persistent embedding service (ENH-003)."
    )
    parser.add_argument(
        "--vault",
        required=True,
        help="Vault directory (resolves embeddings.db + socket name).",
    )
    parser.add_argument("--model", required=True, help="fastembed model id to serve.")
    parser.add_argument(
        "--idle-exit",
        type=int,
        default=_DEFAULT_IDLE_EXIT,
        help="Exit after N idle seconds (default 600).",
    )
    args = parser.parse_args()

    vault = Path(args.vault).expanduser()
    sock_path = socket_path(vault)
    pid_file = pid_path(vault)
    our_pid = os.getpid()

    # Singleton guard: a live daemon for this vault already owns the socket.
    existing = _read_pid(pid_file)
    if existing and pid_alive(existing):
        return 0

    _write_pid(pid_file, our_pid)
    # Reclaim a stale socket left by a crashed predecessor.
    if sock_path.exists():
        try:
            sock_path.unlink()
        except OSError:
            pass

    # Preload the model before binding: the socket's presence then means "warm
    # and ready", so any client that connects gets a fast response instead of
    # waiting on a ~67 MB cold load inside its first request.
    try:
        _get_model(args.model)
    except Exception as e:  # noqa: BLE001
        print(f"embed service: model load failed: {e}", file=sys.stderr)
        _cleanup(sock_path, pid_file, our_pid)
        return 1

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        srv.bind(str(sock_path))
        os.chmod(sock_path, 0o600)
        srv.listen(8)
    except OSError as e:
        print(f"embed service: could not bind {sock_path}: {e}", file=sys.stderr)
        _cleanup(sock_path, pid_file, our_pid)
        return 1

    print(
        f"embed service ready: {sock_path} (model={args.model}, idle_exit={args.idle_exit}s)",
        file=sys.stderr,
    )
    last_activity = time.monotonic()
    srv.settimeout(5.0)

    # Treat SIGTERM like Ctrl-C so the finally below unlinks the socket/pid —
    # Python's default SIGTERM disposition exits without running cleanup, which
    # would leave stale files until the next start reclaims them.
    def _on_term(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)
    try:
        while True:
            if time.monotonic() - last_activity > args.idle_exit:
                break
            try:
                conn, _ = srv.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            last_activity = time.monotonic()
            _handle(conn, args.model)
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        _cleanup(sock_path, pid_file, our_pid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
