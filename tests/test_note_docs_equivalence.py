"""Byte-equivalence proof: docs from note_index rows == the note-parse docs.

The card's hard criterion: CLAUDE.md, TAGS.md and the per-folder
MANIFEST.md files generated from the indexed rows (``render_docs_from_rows``
over :func:`load_note_index_rows` output) must be byte-for-byte identical to
the generation that parses every note file (``build_index`` + the emitters).
Both sides run on the same fixture vault with a fixed ``now`` so cutoffs and
header stamps match, and the rows side goes through a real SQLite round-trip
(full write -> reload), which is exactly the incremental run's data path.

The fixture includes a stem-collision pair (``dup`` in A/ and B/): the DB
keeps the winner in ``note_index`` and the loser in ``note_variants``, and
the tests assert the loser survives into the row-generated manifests --
docs-from-rows stays per-FILE complete.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys

import pytest
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import update_index  # noqa: E402
from parsidion.core.note_docs import (  # noqa: E402
    build_manifests,
    build_tags_md,
    load_note_index_rows,
    render_docs_from_rows,
)
from parsidion.core.note_index import (  # noqa: E402
    _write_note_index_to_db,
    ensure_note_index_schema,
)

_NOW = datetime(2026, 11, 16, 12, 0, 0)
_NOW_STR = _NOW.strftime("%Y-%m-%d %H:%M")


def _note(vault: Path, rel: str, body: str, mtime: float | None = None) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


def _fm(
    title: str,
    tags: list[str],
    related: list[str] | None = None,
    status: str | None = None,
    summary: str | None = None,
) -> str:
    rel = ", ".join(f'"[[{r}]]"' for r in (related or []))
    status_line = f"status: {status}\n" if status else ""
    body_line = summary if summary else f"Body of {title}."
    return (
        "---\ndate: 2026-11-01\ntype: pattern\nconfidence: high\n"
        f"tags: [{', '.join(tags)}]\nrelated: [{rel}]\n{status_line}---\n\n"
        f"# {title}\n\n{body_line}\n"
    )


def _build_fixture(vault: Path) -> None:
    """A small vault exercising every doc-render input, including a stem collision."""
    stale_ts = (_NOW - timedelta(days=60)).timestamp()
    recent_ts = (_NOW - timedelta(hours=1)).timestamp()
    # Two notes share the tie-prone tag pair [cache, perf]; frontmatter lists
    # them UNSORTED to pin the canonical (sorted) tie order on both sides.
    _note(vault, "Patterns/a-cache.md", _fm("A Cache", ["cache", "perf"]))
    _note(vault, "Patterns/b-perf.md", _fm("B Perf", ["perf", "cache"]))
    # Recent + stale, in another folder, with a pipe in the summary.
    _note(
        vault,
        "Debugging/c-recent.md",
        _fm("C Recent", ["debug"], summary="pipes | escape"),
        mtime=recent_ts,
    )
    _note(vault, "Debugging/d-stale.md", _fm("D Stale", ["old"]), mtime=stale_ts)
    # Superseded note still renders in docs (historical parse behaviour).
    _note(
        vault,
        "Research/e-superseded.md",
        _fm("E Sup", ["research"], status="superseded"),
    )
    # Stem collision: same stem in two folders; B/dup.md wins (path order).
    _note(vault, "A/dup.md", _fm("Dup in A", ["dup-tag"], summary="loser summary"))
    _note(vault, "B/dup.md", _fm("Dup in B", ["dup-tag"], summary="winner summary"))
    # Doctor state drives CLAUDE.md's health line in both generations.
    (vault / "doctor_state.json").write_text(
        '{"last_run": "2026-11-15T03:00:00", "notes": {"x": {"status": "ok"}}}',
        encoding="utf-8",
    )


def _read_manifests(vault: Path, folders: list[str]) -> dict[str, str]:
    return {
        folder: (vault / folder / "MANIFEST.md").read_text(encoding="utf-8")
        for folder in folders
    }


def _generation_docs(vault: Path) -> tuple[dict[str, str], str]:
    """Row side: full write -> reload -> render (the incremental data path)."""
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    try:
        ensure_note_index_schema(conn)
    finally:
        conn.close()
    (
        _content,
        note_count,
        _tag_count,
        _folder_notes,
        db_rows,
        _counter,
    ) = update_index.build_index(vault=vault, now=_NOW)
    written = _write_note_index_to_db(db_rows, {r.stem for r in db_rows}, vault=vault)
    assert written
    rows = load_note_index_rows(vault)
    assert rows is not None
    claude_md, tags_md, folder_notes, _counter = render_docs_from_rows(
        rows, vault=vault, total_notes=note_count, now=_NOW
    )
    build_manifests(folder_notes, vault=vault, now_str=_NOW_STR)
    manifests = _read_manifests(vault, sorted(folder_notes))
    return {"claude": claude_md, "tags": tags_md, **manifests}, claude_md


def _parse_docs(vault: Path) -> dict[str, str]:
    """Parse side: build_index structures through the same emitters."""
    (
        content,
        _note_count,
        _tag_count,
        folder_notes,
        _db_rows,
        tag_counter,
    ) = update_index.build_index(vault=vault, now=_NOW)
    build_tags_md(tag_counter, _NOW_STR)
    build_manifests(folder_notes, vault=vault, now_str=_NOW_STR)
    manifests = _read_manifests(vault, sorted(folder_notes))
    tags_md = build_tags_md(tag_counter, _NOW_STR)
    return {"claude": content, "tags": tags_md, **manifests}


def test_docs_byte_equivalence(tmp_path: Path) -> None:
    """All three doc artifacts: rows generation == parse generation, byte for byte."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _build_fixture(vault)
    parse_docs = _parse_docs(vault)
    row_docs, _ = _generation_docs(vault)
    assert parse_docs.keys() == row_docs.keys()
    for name in parse_docs:
        assert row_docs[name] == parse_docs[name], f"divergence in {name}"


def test_collision_loser_survives_in_row_docs(tmp_path: Path) -> None:
    """The stem-collision loser keeps its manifest entry and tag count."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _build_fixture(vault)
    row_docs, _ = _generation_docs(vault)
    parse_docs = _parse_docs(vault)
    # The loser's distinct summary must appear in both generations' manifests.
    assert "loser summary" in parse_docs["A"]
    assert "loser summary" in row_docs["A"]
    assert "winner summary" in row_docs["B"]
    # Variants carry it in the store.
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    try:
        losers = conn.execute(
            "SELECT path, summary FROM note_variants WHERE stem = 'dup'"
        ).fetchall()
    finally:
        conn.close()
    assert len(losers) == 1
    assert losers[0][1] == "loser summary"


@pytest.mark.parametrize("mode", ["--full", "--incremental"])
@pytest.mark.parametrize("folder", ["Debugging", "Tools", "Custom"])
def test_relocating_only_note_empties_generated_manifest(
    tmp_path: Path, mode: str, folder: str
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "config.yaml").write_text(
        "embeddings:\n  enabled: false\ngit:\n  auto_commit: false\n",
        encoding="utf-8",
    )
    config_home = tmp_path / "config"
    registry = config_home / "parsidion/vaults.yaml"
    registry.parent.mkdir(parents=True)
    registry.write_text(f"default: test\nvaults:\n  test: {vault}\n", encoding="utf-8")
    # A real DB makes --incremental exercise the row-driven rendering path.
    sqlite3.connect(vault / "embeddings.db").close()
    note = _note(vault, f"{folder}/only-note.md", _fm("Only Note", ["test"]))

    def rebuild() -> str:
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPTS_DIR / "update_index.py"),
                "--vault",
                str(vault),
                mode,
            ],
            env={
                **os.environ,
                "PARSIDION_STATIC_ONLY": "1",
                "XDG_CONFIG_HOME": str(config_home),
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    rebuild()
    manifest = vault / folder / "MANIFEST.md"
    assert "[[only-note]]" in manifest.read_text(encoding="utf-8")
    destination = vault / "Patterns" / note.name
    destination.parent.mkdir(exist_ok=True)
    note.rename(destination)
    output = rebuild()
    if mode == "--incremental":
        assert "IndexFallback" not in output
    content = manifest.read_text(encoding="utf-8")
    assert "[[only-note]]" not in content
    assert "(0 notes)" in content
    assert "[[only-note]]" in (destination.parent / "MANIFEST.md").read_text(
        encoding="utf-8"
    )


def test_empty_manifest_refresh_preserves_unowned_and_excluded_files(
    tmp_path: Path,
) -> None:
    generated = (
        "# Notes — Vault Notes (1 notes)\n"
        "<!-- Auto-generated by update_index.py on 2026-11-16 12:00 — do not edit manually -->\n"
        "\n| [[old-note]] | | |\n"
    )
    preserved: dict[Path, str] = {}
    for folder in ["Templates", ".trash", ".obsidian", ".git", "TagsRoutes"]:
        path = _note(tmp_path, f"{folder}/MANIFEST.md", generated)
        preserved[path] = generated
    user_content = "# My manifest\n\nKeep these personal notes.\n"
    path = _note(tmp_path, "Personal/MANIFEST.md", user_content)
    preserved[path] = user_content
    (tmp_path / "Empty").mkdir()

    assert build_manifests({"Personal": [], "Empty": []}, tmp_path, _NOW_STR) == []
    for path, content in preserved.items():
        assert path.read_text(encoding="utf-8") == content
    assert not (tmp_path / "Empty/MANIFEST.md").exists()
