"""rebuild_index and vault_doctor MCP tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import vault_common
import vault_path

# ARC-021: resolve SCRIPTS_DIR from the imported package's __file__ rather
# than the hardwired ``~/.claude/skills/parsidion/scripts`` constant in
# vault_path.py. The MCP server imports vault_common/vault_path via the
# editable install, so vault_path.__file__ points at the *same* code the
# process is running — subprocessing it (rather than a possibly-drifted
# ~/.claude copy) keeps import and subprocess consistent. On Unix this is
# the same path because the installer symlinks ~/.claude/skills/parsidion
# at the repo; on Windows (where the installer copies) this matters.
SCRIPTS_DIR: Path = Path(vault_path.__file__).resolve().parent


class OpsToolError(Exception):
    """Raised when an ops MCP tool encounters an error."""


def _resolve_vault_path(vault: str | None) -> Path | None:
    """Resolve an optional vault reference (name or path) to a Path.

    Returns None when *vault* is None so callers can decide whether to pass
    ``--vault`` at all (subprocess) or let resolve_vault() pick the default.
    """
    if vault is None:
        return None
    return vault_common.resolve_vault(explicit=vault)


def rebuild_index(vault: str | None = None) -> str:
    """Rebuild the vault index (CLAUDE.md, MANIFEST.md files, note_index table).

    Args:
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Script output on success.

    Raises:
        OpsToolError: On command failure, timeout, or missing binary.
    """
    script = SCRIPTS_DIR / "update_index.py"
    argv: list[str] = ["uv", "run", "--no-project", str(script)]
    # ARC-021: thread the explicit vault into the subprocess so multi-vault
    # users reach the right index. Without this, the MCP layer always
    # rebuilds the default vault regardless of which vault the caller asked
    # about.
    resolved_vault = _resolve_vault_path(vault)
    if resolved_vault is not None:
        argv.extend(["--vault", str(resolved_vault)])
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise OpsToolError(output)
        return output or "Index rebuilt successfully."
    except subprocess.TimeoutExpired:
        raise OpsToolError("command timed out after 30s")
    except FileNotFoundError as exc:
        raise OpsToolError(str(exc)) from exc
    except OSError as exc:
        raise OpsToolError(str(exc)) from exc


def vault_doctor(
    fix: bool = False,
    errors_only: bool = False,
    limit: int | None = None,
    vault: str | None = None,
) -> str:
    """Scan vault notes for structural issues; optionally repair them.

    Args:
        fix: When True, repair repairable issues via Claude haiku.
             When False, scan and report only (--fix flag is omitted).
        errors_only: When True, skip warnings and report errors only.
        limit: Maximum number of notes to repair (only relevant when fix=True).
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Scan/repair report.

    Raises:
        OpsToolError: On command failure, timeout, or missing binary.
    """
    script = SCRIPTS_DIR / "vault_doctor.py"
    args: list[str] = ["uv", "run", "--no-project", str(script)]
    if fix:
        args.append("--fix")
    if errors_only:
        args.append("--errors-only")
    if limit is not None:
        args.extend(["--limit", str(limit)])
    # ARC-021: thread the explicit vault into the subprocess.
    resolved_vault = _resolve_vault_path(vault)
    if resolved_vault is not None:
        args.extend(["--vault", str(resolved_vault)])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise OpsToolError(output)
        return output or "Doctor scan complete."
    except subprocess.TimeoutExpired:
        raise OpsToolError("command timed out after 120s")
    except FileNotFoundError as exc:
        raise OpsToolError(str(exc)) from exc
    except OSError as exc:
        raise OpsToolError(str(exc)) from exc
