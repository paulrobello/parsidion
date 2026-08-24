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

    ARC-004: the subprocess itself is owned by
    ``core.vault_index.run_index_rebuild`` (shared with the installer and the
    summarizer); this wrapper keeps the MCP-specific contract — SCRIPTS_DIR
    resolves to the imported package's own directory (ARC-021, so the
    subprocess runs the same code the server imported) and errors surface as
    ``OpsToolError``.

    Args:
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Script output on success.

    Raises:
        OpsToolError: On command failure, timeout, or missing binary.
    """
    resolved_vault = _resolve_vault_path(vault)
    reason, proc = vault_common.run_index_rebuild(
        resolved_vault, scripts_dir=SCRIPTS_DIR, timeout=30.0
    )
    if reason == "ok" and proc is not None:
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise OpsToolError(output)
        return output or "Index rebuilt successfully."
    if reason == "timeout":
        raise OpsToolError("command timed out after 30s")
    raise OpsToolError("uv or update_index.py not found")


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


def vault_health(vault: str | None = None, *, fast: bool = False) -> str:
    """Return the composite vault health report as JSON (ENH-007).

    Seven scored dimensions (index freshness, queue health, graph
    connectivity, metadata quality, embedding coverage, tag hygiene, file
    hygiene) combined into a weighted overall grade. Each dimension carries
    a concrete ``action`` command when unhealthy, or ``null`` when healthy.

    Read-only — never mutates the vault. Subprocesses ``vault-stats
    --health --json`` so the import and subprocess layers see the same code
    (same pattern as ``rebuild_index`` / ``vault_doctor``).

    Args:
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.
        fast: When True, skip the metadata-quality scan so the report returns
            in well under a second on large vaults. The metadata dimension
            reports ``detail='skipped (--fast)'`` with a neutral score.

    Returns:
        The health report as a JSON string (compact, sorted keys).

    Raises:
        OpsToolError: On command failure, timeout, or missing binary.
    """
    script = SCRIPTS_DIR / "vault_stats.py"
    args: list[str] = [
        "uv",
        "run",
        "--no-project",
        str(script),
        "--health",
        "--json",
    ]
    if fast:
        args.append("--fast")
    # ARC-021: thread the explicit vault into the subprocess so multi-vault
    # users reach the right report regardless of which vault the caller asked
    # about.
    resolved_vault = _resolve_vault_path(vault)
    if resolved_vault is not None:
        args.extend(["--vault", str(resolved_vault)])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise OpsToolError(output)
        return output or '{"error": "vault-stats produced no output"}'
    except subprocess.TimeoutExpired:
        raise OpsToolError("command timed out after 60s")
    except FileNotFoundError as exc:
        raise OpsToolError(str(exc)) from exc
    except OSError as exc:
        raise OpsToolError(str(exc)) from exc
