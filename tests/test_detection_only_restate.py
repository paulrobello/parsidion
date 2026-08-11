"""Detection-only frontmatter defects must keep re-reporting until fixed.

``should_skip`` treats state status ``"skipped"`` as permanent — unlike
``"ok"``, which expires after ``STATE_STALE_DAYS``.  The five malformed-
frontmatter codes are deliberately absent from ``REPAIRABLE_CODES`` (the AI
repair path is not turned loose on them), so a note carrying only those lands
in ``manual_only``.  Recording it ``skipped`` would announce the defect on one
run and hide it forever after — the stale-state blindspot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_doctor  # noqa: E402
from doctor._state import DETECTION_ONLY_CODES, REPAIRABLE_CODES, Issue  # noqa: E402
from doctor.orchestrator import _classify_repair_candidates  # noqa: E402


def test_skipped_status_is_permanent() -> None:
    """The premise: 'skipped' never expires, so it must not be set lightly."""
    assert vault_doctor.should_skip("n.md", {"notes": {"n.md": {"status": "skipped"}}})


def test_detection_only_codes_are_not_repairable() -> None:
    """They must stay out of the AI repair path for the exemption to matter."""
    assert not (DETECTION_ONLY_CODES & REPAIRABLE_CODES)


def _classify(issues_by_note: dict[Path, list[Issue]], vault: Path) -> dict:
    state: dict = {"notes": {}}
    _classify_repair_candidates(issues_by_note, state, "2026-08-10", vault)
    return state


@pytest.mark.parametrize("code", sorted(DETECTION_ONLY_CODES))
def test_detection_only_note_is_left_stateless(tmp_path: Path, code: str) -> None:
    note = tmp_path / "Patterns" / "bad.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ndate: 2026-08-10\n---\n\n# Bad\n", encoding="utf-8")
    issues = [Issue(note, "warning", code, "detected")]

    state = _classify({note: issues}, tmp_path)

    assert state["notes"] == {}, (
        f"{code} was recorded as skipped and would never be re-reported"
    )


def test_ordinary_manual_only_note_is_still_recorded(tmp_path: Path) -> None:
    """The exemption must not disable state recording generally."""
    note = tmp_path / "Daily" / "2026-08-10.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ndate: 2026-08-10\n---\n\n# Flat\n", encoding="utf-8")
    issues = [Issue(note, "warning", "FLAT_DAILY", "flat daily note")]

    state = _classify({note: issues}, tmp_path)

    assert state["notes"], "a genuinely manual-only note should still be recorded"
    assert next(iter(state["notes"].values()))["status"] == "skipped"


def test_mixed_note_with_a_detection_only_code_is_left_stateless(
    tmp_path: Path,
) -> None:
    """A detection-only defect anywhere on the note keeps it in the report."""
    note = tmp_path / "Daily" / "2026-08-10.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\ndate: 2026-08-10\n---\n\n# Flat\n", encoding="utf-8")
    issues = [
        Issue(note, "warning", "FLAT_DAILY", "flat daily note"),
        Issue(note, "warning", "SCALAR_LIST_FIELD", "tags is a scalar"),
    ]

    state = _classify({note: issues}, tmp_path)

    assert state["notes"] == {}
