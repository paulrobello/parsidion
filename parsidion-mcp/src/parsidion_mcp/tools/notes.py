"""vault_read and vault_write MCP tools."""

from __future__ import annotations

import os
from pathlib import Path

import vault_common


class VaultToolError(Exception):
    """Raised when a vault MCP tool encounters an error."""


def _resolve_vault_path(path: str, vault: str | None = None) -> Path:
    """Resolve *path* against vault root; raise VaultToolError if it escapes.

    Args:
        path: Path string, relative to vault root or absolute.
        vault: Optional vault reference (name from vaults.yaml, or path).

    Returns:
        Resolved absolute Path inside vault root.

    Raises:
        VaultToolError: If the resolved path escapes the vault root.
    """
    # ARC-004: Use resolve_vault() instead of the module-level VAULT_ROOT
    # constant so that CLAUDE_VAULT env var, project-local vault files,
    # and named vaults are all respected.
    # ARC-021: thread the optional *vault* reference through so the MCP
    # caller can target a specific named vault instead of the default.
    vault_root = vault_common.resolve_vault(explicit=vault).resolve()
    raw = Path(path)
    candidate = (raw if raw.is_absolute() else vault_root / raw).resolve()
    if not candidate.is_relative_to(vault_root):
        raise VaultToolError("path escapes vault root")
    return candidate


def _validate_note_segments(resolved: Path, vault_root: Path, *, action: str) -> None:
    """SEC-201: reject hidden or excluded-dir note locations (shared gate).

    The segment policy both vault_read (SEC-008) and vault_write (SEC-201)
    enforce: no path segment may start with ``.`` (dotfiles, dot-dirs such as
    ``.git``/``.trash``/``.obsidian``) and the top-level folder may not be in
    ``EXCLUDE_DIRS``. Sharing one helper keeps the read and write gates from
    drifting apart again — the write side previously enforced only containment
    + ``.md`` suffix, so writes into ``.trash/backup/**`` (the SEC-107
    pre-mutation backups) or ``.obsidian/**`` were accepted.

    Args:
        resolved: Resolved absolute path inside *vault_root*.
        vault_root: Resolved vault root.
        action: Verb for the error message (``"readable"``/``"writable"``).

    Raises:
        VaultToolError: When any segment is hidden or the top-level folder is
            excluded.
    """
    rel = resolved.relative_to(vault_root)
    segments = rel.parts
    if any(segment.startswith(".") for segment in segments):
        raise VaultToolError(f"Hidden paths are not {action}")
    if segments and segments[0] in vault_common.EXCLUDE_DIRS:
        raise VaultToolError(f"Excluded directory: {segments[0]}")


def _validate_readable_note(resolved: Path, vault_root: Path) -> None:
    """SEC-008: restrict vault_read to markdown notes in the note tree.

    Mirrors the vault_write rules: ``.md`` suffix only, no dotfile/dot-dir
    segments, no ``EXCLUDE_DIRS`` top-level folder. Without this, a caller
    could read ``config.local.yaml`` (the documented home for
    ``ANTHROPIC_API_KEY``), ``.git/config``, ``hook_events.log``, or
    ``pending_summaries.jsonl`` through the MCP read tool.

    Raises:
        VaultToolError: When the path is not a readable note location.
    """
    if resolved.suffix.lower() != ".md":
        raise VaultToolError("Only .md files are readable")
    _validate_note_segments(resolved, vault_root, action="readable")


def vault_read(path: str, vault: str | None = None) -> str:
    """Read a vault note by path.

    Args:
        path: Path relative to vault root (e.g. ``Patterns/my-note.md``) or absolute.
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Full note content (frontmatter + body).

    Raises:
        VaultToolError: On any read failure (missing vault, path escape,
            non-note path, oversized file, binary content, file not found,
            OS error).
    """
    vault_root = vault_common.resolve_vault(explicit=vault)
    if not vault_root.exists():
        raise VaultToolError(f"vault root not found at {vault_root}")
    try:
        resolved = _resolve_vault_path(path, vault=vault)
        # SEC-008: same note-only rule the write side enforces.
        _validate_readable_note(resolved, vault_root.resolve())
        # SEC-008: size cap before reading (self-DoS guard).
        if resolved.stat().st_size > _MAX_CONTENT_BYTES:
            raise VaultToolError("Note exceeds 10 MB limit")
        return resolved.read_text(encoding="utf-8")
    except VaultToolError:
        raise
    except UnicodeDecodeError as exc:
        raise VaultToolError("not a text note") from exc
    except FileNotFoundError:
        raise VaultToolError(f"note not found at {path}")
    except OSError as exc:
        raise VaultToolError(str(exc)) from exc


_MAX_CONTENT_BYTES: int = 10 * 1024 * 1024  # 10 MB


def vault_write(path: str, content: str, vault: str | None = None) -> str:
    """Create or overwrite a vault note.

    Args:
        path: Path relative to vault root.
        content: Full note content including YAML frontmatter.
        vault: Optional vault reference (name from vaults.yaml, or absolute path).
            When None, the resolver's default precedence applies.

    Returns:
        Success message with absolute path.

    Raises:
        VaultToolError: On any write failure (path escape, OS error, oversized
            content, or non-.md extension).
    """
    try:
        # SEC-006: Reject oversized content before writing.
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            raise VaultToolError("Content exceeds 10 MB limit")
        resolved = _resolve_vault_path(path, vault=vault)
        # SEC-009: Only allow .md file extensions.
        if resolved.suffix.lower() != ".md":
            raise VaultToolError("Only .md files are allowed")
        vault_root = vault_common.resolve_vault(explicit=vault).resolve()
        # SEC-201: apply the same hidden-path/excluded-dir segment gate the
        # read side enforces, before any filesystem access — without it a
        # write to .trash/backup/<date>/**.md could overwrite the SEC-107
        # pre-mutation backups, and .obsidian/.git stashed content where the
        # indexer/doctor/health never look.
        _validate_note_segments(resolved, vault_root, action="writable")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        # SEC-P003: close the TOCTOU window between the containment check
        # above and the actual write. Re-resolve + re-validate against the
        # vault root (catches a parent-dir symlink swap), then open the leaf
        # with O_NOFOLLOW (blocks a leaf symlink swap) and write through the
        # resulting fd so a later swap cannot redirect the bytes. Any OSError
        # from the open or write is converted to VaultToolError below.
        fresh = resolved.resolve()
        if not fresh.is_relative_to(vault_root):
            raise VaultToolError("path escapes vault root")
        # SEC-201: re-run the segment gate on the re-resolved path so a
        # vault-internal symlink swap cannot redirect the write into a hidden
        # or excluded directory that containment alone permits.
        _validate_note_segments(fresh, vault_root, action="writable")
        fd = os.open(
            str(fresh),
            os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_TRUNC,
            0o644,
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(content.encode("utf-8"))
        except BaseException:
            # Best-effort cleanup if os.fdopen failed before taking ownership
            # of the fd. On the success path the with-block closed it already.
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        return f"Written: {resolved}"
    except VaultToolError:
        raise
    except OSError as exc:
        raise VaultToolError(str(exc)) from exc
