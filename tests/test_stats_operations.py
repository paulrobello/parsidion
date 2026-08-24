"""QA-015: direct tests for ``cli/stats/operations.py``.

The module previously had no test file referencing it.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.stats.operations import run_hooks


def _write_hook_log(vault: Path, events: list[dict[str, object]]) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "hook_events.log").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8"
    )


def test_run_hooks_prints_table_with_last_events(tmp_path: Path, capsys) -> None:
    events = [
        {
            "hook": "SessionStart",
            "ts": "2026-08-23T10:00:00",
            "project": "alpha",
            "duration_ms": 120,
            "notes_injected": 4,
        },
        {
            "hook": "SessionEnd",
            "ts": "2026-08-23T11:00:00",
            "project": "beta",
            "duration_ms": 60,
            "queued": True,
        },
        {
            "hook": "SessionStart",
            "ts": "2026-08-23T12:00:00",
            "project": "alpha",
            "duration_ms": 90,
        },
    ]
    _write_hook_log(tmp_path, events)

    run_hooks(last_n=2, vault=tmp_path)
    out = capsys.readouterr().out
    # Header reports the requested slice of the total.
    assert "last 2 of 3 total" in out
    # Only the two most recent events are shown.
    assert "12:00:00" in out
    assert "11:00:00" in out
    assert "10:00:00" not in out
    # Extra fields render as key=value.
    assert "queued=True" in out


def test_run_hooks_missing_log_prints_notice(tmp_path: Path, capsys) -> None:
    run_hooks(last_n=5, vault=tmp_path)
    out = capsys.readouterr().out
    assert "No hook_events.log found" in out


def test_run_hooks_empty_log_prints_notice(tmp_path: Path, capsys) -> None:
    _write_hook_log(tmp_path, [])
    run_hooks(last_n=5, vault=tmp_path)
    out = capsys.readouterr().out
    assert "empty" in out
