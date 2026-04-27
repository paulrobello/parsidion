"""Adaptive context scoring -- per-note usefulness tracking.

Tracks which vault notes are referenced during sessions and maintains
hit/miss counts and last-seen timestamps per project. Used by the
session_start_hook to prioritize frequently-useful notes.

This module is part of the vault_common split (ARC-005).  All public symbols
are re-exported from ``vault_common`` for backward compatibility.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

__all__: list[str] = [
    # Last-seen tracking
    "get_last_seen_path",
    "load_last_seen",
    "save_last_seen",
    # Usefulness scoring
    "get_usefulness_path",
    "load_usefulness_scores",
    "save_injected_notes",
    "update_usefulness_scores",
    "get_injected_stems",
]

# ---------------------------------------------------------------------------
# Per-project last-seen tracking (#10 cross-session delta)
# ---------------------------------------------------------------------------

_LAST_SEEN_FILENAME = "last_seen.json"


def get_last_seen_path(vault: Path | None = None) -> Path:
    """Return the path to the last-seen tracker JSON file.

    Args:
        vault: Optional vault path (unused, for API consistency).

    Returns:
        Path to ``~/.claude/vault_last_seen.json`` (vault-independent location).
    """
    # last_seen.json is stored outside the vault to track across all vaults
    return Path.home() / ".claude" / _LAST_SEEN_FILENAME


def load_last_seen(vault: Path | None = None) -> dict[str, str]:
    """Load the per-project last-seen timestamp map.

    Args:
        vault: Optional vault path (unused, for API consistency).

    Returns:
        Dict mapping project name -> ISO 8601 timestamp string.
        Returns empty dict when the file is absent or unreadable.
    """
    path = get_last_seen_path(vault)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def save_last_seen(
    project: str, ts: str | None = None, vault: Path | None = None
) -> None:
    """Update and persist the last-seen timestamp for *project*.

    Args:
        project: Project name to update.
        ts: ISO 8601 timestamp. Defaults to ``datetime.now().isoformat()``.
        vault: Optional vault path (unused, for API consistency).
    """
    if ts is None:
        ts = datetime.now().isoformat(timespec="seconds")
    path = get_last_seen_path(vault)
    try:
        data = load_last_seen(vault)
        data[project] = ts
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Adaptive context (#17) -- per-note usefulness tracking
# ---------------------------------------------------------------------------

_NOTE_USEFULNESS_FILENAME = "note_usefulness.json"


def get_usefulness_path() -> Path:
    """Return path to the per-note usefulness scores JSON file.

    Returns:
        Path to ``~/.claude/note_usefulness.json``.
    """
    return Path.home() / ".claude" / _NOTE_USEFULNESS_FILENAME


def load_usefulness_scores() -> dict[str, dict]:
    """Load per-note usefulness stats.

    Each entry has keys: ``hits`` (int), ``misses`` (int),
    ``last_hit`` (ISO 8601 str | None).

    Returns:
        Dict mapping note stem -> stats dict. Empty when absent or unreadable.
    """
    path = get_usefulness_path()
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw  # type: ignore[return-value]
        return {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def get_injected_stems(project: str) -> list[str]:
    """Return the list of note stems injected in the previous session for *project*.

    Reads from ``last_seen.json`` where injected stems are stored under the key
    ``{project}__injected``.

    Args:
        project: Project name.

    Returns:
        List of note stem strings, or empty list when not recorded.
    """
    data = load_last_seen()
    raw = data.get(f"{project}__injected", "")
    if not raw:
        return []
    # Stored as comma-separated stems
    return [s.strip() for s in raw.split(",") if s.strip()]


def save_injected_notes(project: str, stems: list[str]) -> None:
    """Persist the list of note stems injected for *project*.

    Stored alongside the last-seen timestamp in ``last_seen.json`` under
    the key ``{project}__injected`` as a comma-separated string.

    Args:
        project: Project name.
        stems: List of note stems that were injected into context.
    """
    path = get_last_seen_path()
    try:
        data = load_last_seen()
        data[f"{project}__injected"] = ",".join(stems)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def update_usefulness_scores(
    referenced_stems: set[str],
    injected_stems: list[str],
) -> None:
    """Update hit/miss counts for notes based on session references.

    Notes in *injected_stems* that appear in *referenced_stems* get a hit
    increment; those not referenced get a miss increment.  Best-effort --
    never raises.

    Args:
        referenced_stems: Set of note stems mentioned during the session.
        injected_stems: List of stems that were injected at session start.
    """
    if not injected_stems:
        return
    path = get_usefulness_path()
    try:
        scores = load_usefulness_scores()
        now_ts = datetime.now().isoformat(timespec="seconds")
        for stem in injected_stems:
            entry = scores.setdefault(stem, {"hits": 0, "misses": 0, "last_hit": None})
            if stem in referenced_stems:
                entry["hits"] = entry.get("hits", 0) + 1
                entry["last_hit"] = now_ts
            else:
                entry["misses"] = entry.get("misses", 0) + 1
        path.write_text(json.dumps(scores, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
