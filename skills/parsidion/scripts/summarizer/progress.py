"""Progress-file writer for ``vault-stats --summarizer-progress``.

Extracted from ``summarize_sessions.py`` (ARC-009).
"""

from __future__ import annotations

import json
from datetime import datetime

import vault_common

# Progress tracking (#13)
_PROGRESS_FILE = vault_common.secure_log_dir() / "parsidion-summarizer-progress.json"


def _write_progress(
    total: int,
    processed: int,
    written: int,
    skipped: int,
    errors: int,
    current: str = "",
) -> None:
    """Write current summarizer progress to a temp file for vault-stats --summarizer-progress.

    Best-effort — never raises.

    Args:
        total: Total sessions to process.
        processed: Sessions completed (written + skipped + errors).
        written: Notes actually written.
        skipped: Sessions skipped by write-gate.
        errors: Sessions that failed.
        current: Short description of session currently being processed.
    """
    try:
        data = {
            "total": total,
            "processed": processed,
            "written": written,
            "skipped": skipped,
            "errors": errors,
            "current": current,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        _PROGRESS_FILE.write_text(json.dumps(data) + "\n", encoding="utf-8")
    except OSError:
        pass


def _clear_progress() -> None:
    """Remove the progress file when the summarizer finishes.

    Best-effort — never raises.
    """
    try:
        _PROGRESS_FILE.unlink(missing_ok=True)
    except OSError:
        pass
