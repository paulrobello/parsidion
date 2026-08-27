"""QA-111: happy-path tests for the one-time migration tools.

``tools/migrate_memory.py`` and ``tools/migrate_research.py`` write into
the vault but had zero test coverage. Per the remediation playbook the
default decision is *keep + test*: each test builds a tmp source
structure, runs the tool's ``main()`` in ``--execute`` mode against a tmp
vault, and asserts the expected notes exist with valid frontmatter.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import vault_common

_TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

import migrate_memory  # noqa: E402 -- needs _TOOLS_DIR on sys.path first
import migrate_research  # noqa: E402


def test_migrate_memory_happy_path(
    tmp_vault: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A global memory file's ## sections become classified vault notes."""
    claude_home = tmp_path / "claude-home"
    global_memory = claude_home / "memory"
    global_memory.mkdir(parents=True)
    (global_memory / "notes.md").write_text(
        "## Fix login crash\n\n"
        "The login crash was caused by a missing guard. Adding the guard\n"
        "fixed the bug.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(migrate_memory, "CLAUDE_DIR", claude_home)
    monkeypatch.setattr(migrate_memory, "GLOBAL_MEMORY_DIR", global_memory)
    monkeypatch.setattr(
        migrate_memory, "PROJECTS_DIR", claude_home / "projects"
    )  # absent -> no per-project dirs discovered
    monkeypatch.setattr(migrate_memory, "VAULT_ROOT", tmp_vault)
    monkeypatch.setattr(sys, "argv", ["migrate_memory.py", "--execute"])

    migrate_memory.main()

    out = capsys.readouterr().out
    assert "Migration complete" in out

    # Original memory file was backed up (renamed to .bak)
    assert (global_memory / "notes.md.bak").is_file()

    # One note written, classified into Debugging/ by the keyword heuristics
    notes = list(tmp_vault.glob("Debugging/*.md"))
    assert len(notes) == 1
    content = notes[0].read_text(encoding="utf-8")
    assert "# Fix login crash" in content

    fm = vault_common.parse_frontmatter(content)
    assert fm.get("type") == "debugging"
    assert fm.get("date")
    tags = fm.get("tags")
    assert isinstance(tags, list) and tags


def test_migrate_research_happy_path(
    tmp_vault: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A mapped research .md lands in Research/ with frontmatter and a
    date-suffix-stripped filename."""
    research = tmp_path / "research"
    research.mkdir()
    (research / "wgpu-28-breaking-changes-2026-01-29.md").write_text(
        "# WGPU 28 Breaking Changes\n\nNotes on the breaking API changes.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(migrate_research, "VAULT_ROOT", tmp_vault)
    monkeypatch.setattr(
        sys, "argv", ["migrate_research.py", str(research), "--execute"]
    )

    migrate_research.main()

    out = capsys.readouterr().out
    assert "Migration complete" in out

    # Date suffix stripped from the destination filename
    note = tmp_vault / "Research" / "wgpu-28-breaking-changes.md"
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "# WGPU 28 Breaking Changes" in content

    fm = vault_common.parse_frontmatter(content)
    assert fm.get("type") == "research"
    # Date extracted from the source filename
    assert fm.get("date") == "2026-01-29"
    tags = fm.get("tags")
    assert isinstance(tags, list) and tags
