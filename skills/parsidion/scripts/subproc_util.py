"""Shared subprocess helper: start_new_session + process-group kill on timeout.

SEC-122 / ARC-048f: ``ai_backend._run_prompt_subprocess`` and
``parmem_backend._run_parmem`` each implemented this pattern separately and
the two had already drifted (different escalation orderings, different
fallback handling). This module is the single canonical implementation.

Both call sites want the same guarantees:

- Start the child in a new session (``start_new_session=True``) so it has its
  own process group that can be killed independently of the parent.
- On ``TimeoutExpired``, escalate SIGTERM → SIGKILL against the whole process
  group (not just ``proc.pid``); a plain ``proc.kill()`` leaves orphaned
  grandchildren behind.
- Never raise — return a tagged result so the caller can decide whether to
  log, fall back, or propagate. The tags are ``"ok" | "launch" | "timeout"``
  with the same shape both backends already used.

Stdlib only — both consumers (the hook scripts and parsidion-mcp subprocess
bridges) are bound by the stdlib-only rule, so this module cannot pull in
any third-party helpers.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

__all__ = [
    "run_with_pgkill",
    "PGKILL_GRACE_SECS",
]


# Seconds to wait between SIGTERM and SIGKILL when a child times out. Long
# enough for a well-behaved child to flush logs and exit on SIGTERM, short
# enough that a wedged child does not stall the caller indefinitely.
PGKILL_GRACE_SECS: float = 5.0


def run_with_pgkill(
    cmd: list[str],
    *,
    cwd: Path | str | None,
    timeout: float,
    env: Mapping[str, str] | None = None,
    stdin: Any = None,
) -> tuple[str, subprocess.CompletedProcess[str] | None]:
    """Run *cmd* in a new session; on timeout, kill the whole process group.

    Returns a ``(reason, proc)`` tuple matching the shape both backends
    already used:

    - ``("ok", CompletedProcess)`` — normal completion (any returncode).
      ``proc.stdout`` and ``proc.stderr`` carry the captured output.
    - ``("launch", None)`` — the binary could not be started (``OSError`` on
      ``Popen``). No process ever ran, so there is no stderr to inspect.
    - ``("timeout", None)`` — *timeout* seconds elapsed. The process group
      was killed (SIGTERM, then SIGKILL after :data:`PGKILL_GRACE_SECS`).
      No captured output is returned because the process never finished.

    Args:
        cmd: Argv list, starting with the binary. The caller is responsible
            for resolving and validating the binary path — this helper does
            not run ``shutil.which`` because each caller has its own
            allowlist/gate (par-mem: ``par_mem.enabled`` + ``/health``;
            ai_backend: a model-tier-aware lookup).
        cwd: Working directory. Mandatory because both call sites pass one
            (par-mem runs against the vault root; ai_backend runs against
            the configured working dir) and an unqualified default of
            ``None`` (inherit) is the wrong shape for an explicit-launch API.
        timeout: Maximum seconds to wait for completion. ``<= 0`` is treated
            as "no timeout" via ``communicate(timeout=None)``.
        env: Optional environment mapping (commonly
            ``env_without_claudecode()``). ``None`` inherits the parent env.
        stdin: Optional stdin to pass to ``Popen.communicate``. Defaults to
            ``None`` (no stdin) — the canonical "no stdin" sentinel both
            backends used.

    Returns:
        ``(reason, proc)`` as described above. Never raises — the caller
        decides whether ``"launch"``/``"timeout"`` warrants a log line or a
        fallback path.
    """
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd is not None else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return "launch", None

    communicate_timeout: float | None = timeout if timeout and timeout > 0 else None
    try:
        stdout, stderr = proc.communicate(input=stdin, timeout=communicate_timeout)
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        return "timeout", None
    except Exception:  # noqa: BLE001 — contract: never raises
        # Defensive: any unexpected error from communicate() (e.g. a closed
        # pipe) is treated as a launch failure so the caller falls back.
        try:
            proc.kill()
        except OSError:
            pass
        return "launch", None

    return "ok", subprocess.CompletedProcess(
        cmd,
        proc.returncode if proc.returncode is not None else 0,
        stdout=stdout,
        stderr=stderr,
    )


def _kill_process_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM → wait PGKILL_GRACE_SECS → SIGKILL the process group of *proc*.

    Best-effort: every OSError (process already gone, EPERM, ESRCH) is
    swallowed because a failed cleanup must never mask the original timeout.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        try:
            proc.wait(timeout=PGKILL_GRACE_SECS)
            return
        except subprocess.TimeoutExpired:
            continue
        except (OSError, ChildProcessError):
            return
