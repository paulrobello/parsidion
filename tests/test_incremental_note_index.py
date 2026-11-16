"""Tests for ENH-067 Task 1: index_state table + mtime-gated change diff."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from parsidion.core.note_index import (  # noqa: E402
    _state_key,
    ensure_index_state_table,
    diff_changed_notes,
)


def _record(vault: Path, note: Path) -> None:
    """Insert an index_state row for *note* as the apply step would."""
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    ensure_index_state_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO index_state VALUES (?, ?, ?, ?)",
        (
            _state_key(vault, note),
            note.stat().st_mtime,
            hashlib.sha256(note.read_bytes()).hexdigest(),
            0.0,
        ),
    )
    conn.commit()
    conn.close()


def _note(vault: Path, rel: str, body: str = "# T\n") -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def test_index_state_table_created(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "e.db"))
    ensure_index_state_table(conn)
    ensure_index_state_table(conn)  # idempotent
    cols = [r[1] for r in conn.execute("PRAGMA table_info(index_state)")]
    conn.close()
    assert cols == ["path", "mtime", "sha256", "indexed_at"]


def test_no_changes_returns_empty_diff(tmp_path: Path) -> None:
    a = _note(tmp_path, "Patterns/a.md")
    _record(tmp_path, a)
    diff = diff_changed_notes(tmp_path)
    assert (diff.added, diff.modified, diff.deleted) == ([], [], [])


def test_modified_note_detected_by_mtime_then_hash(tmp_path: Path) -> None:
    a = _note(tmp_path, "Patterns/a.md", "# one\n")
    _record(tmp_path, a)
    # mtime changes but content identical: not modified
    st = a.stat()
    os.utime(a, (st.st_atime, st.st_mtime + 100))
    assert diff_changed_notes(tmp_path).modified == []
    # content changes: modified
    a.write_text("# two\n", encoding="utf-8")
    os.utime(a, (st.st_atime, st.st_mtime + 200))
    assert diff_changed_notes(tmp_path).modified == [a]


def test_added_and_deleted_detected(tmp_path: Path) -> None:
    a = _note(tmp_path, "Patterns/a.md")
    b = _note(tmp_path, "Patterns/b.md")
    _record(tmp_path, a)
    _record(tmp_path, b)
    b.unlink()
    c = _note(tmp_path, "Debugging/c.md")
    diff = diff_changed_notes(tmp_path)
    assert diff.added == [c]
    assert diff.deleted == [b]
    assert diff.modified == []


def test_ignores_exclude_dirs(tmp_path: Path) -> None:
    _note(tmp_path, "Templates/t.md")
    _note(tmp_path, ".trash/x.md")
    _note(tmp_path, ".obsidian/o.md")
    a = _note(tmp_path, "Patterns/a.md")
    assert diff_changed_notes(tmp_path).added == [a]


def test_rename_detected_as_deleted_and_added(tmp_path: Path) -> None:
    a = _note(tmp_path, "Patterns/a.md")
    _record(tmp_path, a)
    b = tmp_path / "Patterns" / "b.md"
    a.rename(b)
    diff = diff_changed_notes(tmp_path)
    assert diff.added == [b]
    assert diff.deleted == [a]
    assert diff.modified == []


def test_missing_db_reports_all_notes_added(tmp_path: Path) -> None:
    a = _note(tmp_path, "Patterns/a.md")
    b = _note(tmp_path, "Debugging/b.md")
    assert not (tmp_path / "embeddings.db").exists()
    diff = diff_changed_notes(tmp_path)
    assert sorted(diff.added) == sorted([a, b])
    assert diff.modified == [] and diff.deleted == []
    assert not (tmp_path / "embeddings.db").exists()


def test_vault_spelling_does_not_change_keys(tmp_path: Path) -> None:
    real = tmp_path / "real"
    a = _note(real, "Patterns/a.md")
    link = tmp_path / "link"
    link.symlink_to(real)
    _record(real, a)
    diff = diff_changed_notes(link)
    assert (diff.added, diff.modified, diff.deleted) == ([], [], [])


# ---------------------------------------------------------------------------
# Task 2: incremental recompute + reverse-link propagation
# ---------------------------------------------------------------------------

import json  # noqa: E402

from parsidion.core.note_index import apply_incremental_update  # noqa: E402


def _fm(related: list[str], title: str = "T") -> str:
    rel = ", ".join(f'"[[{r}]]"' for r in related)
    return (
        "---\ndate: 2026-01-01\ntype: pattern\ntags: [x]\nconfidence: high\n"
        f"sources: []\nrelated: [{rel}]\n---\n# {title}\n\nBody of {title}.\n"
    )


def _open(vault: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(vault / "embeddings.db"))


def _row(vault: Path, stem: str) -> tuple[int, list[str]] | None:
    conn = _open(vault)
    r = conn.execute(
        "SELECT incoming_links, incoming_stems FROM note_index WHERE stem = ?",
        (stem,),
    ).fetchone()
    conn.close()
    return None if r is None else (r[0], json.loads(r[1]))


def _apply(vault: Path) -> int:
    conn = _open(vault)
    try:
        res = apply_incremental_update(vault, conn)
    finally:
        conn.close()
    assert res.fallback_reason is None
    return res.changed


def _base_vault(vault: Path) -> None:
    _note(vault, "Patterns/a.md", _fm(["b"], "A"))
    _note(vault, "Patterns/b.md", _fm([], "B"))
    _note(vault, "Patterns/c.md", _fm([], "C"))
    assert _apply(vault) == 3


def test_link_add_updates_target_incoming(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    assert _row(tmp_path, "c") == (0, [])
    a = tmp_path / "Patterns" / "a.md"
    a.write_text(_fm(["b", "c"], "A"), encoding="utf-8")
    os.utime(a, (1, a.stat().st_mtime + 50))
    assert _apply(tmp_path) == 1
    assert _row(tmp_path, "c") == (1, ["a"])
    assert _row(tmp_path, "b") == (1, ["a"])


def test_link_remove_updates_target_incoming(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    a = tmp_path / "Patterns" / "a.md"
    a.write_text(_fm([], "A"), encoding="utf-8")
    os.utime(a, (1, a.stat().st_mtime + 50))
    assert _apply(tmp_path) == 1
    assert _row(tmp_path, "b") == (0, [])


def test_deleted_note_drops_row_and_contributions(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    (tmp_path / "Patterns" / "a.md").unlink()
    assert _apply(tmp_path) == 1
    assert _row(tmp_path, "a") is None
    assert _row(tmp_path, "b") == (0, [])
    conn = _open(tmp_path)
    keys = [r[0] for r in conn.execute("SELECT path FROM index_state")]
    conn.close()
    assert sorted(keys) == ["Patterns/b.md", "Patterns/c.md"]


def test_touched_file_mtime_refreshed_in_apply(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    b = tmp_path / "Patterns" / "b.md"
    new_mtime = b.stat().st_mtime + 500
    os.utime(b, (new_mtime, new_mtime))
    assert _apply(tmp_path) == 0
    conn = _open(tmp_path)
    (stored,) = conn.execute(
        "SELECT mtime FROM index_state WHERE path = 'Patterns/b.md'"
    ).fetchone()
    conn.close()
    assert stored == new_mtime
    assert not diff_changed_notes(tmp_path)


def _snapshot(vault: Path) -> list[tuple]:
    conn = _open(vault)
    rows = conn.execute(
        "SELECT stem, path, folder, title, summary, tags, note_type, project,"
        " confidence, mtime, related, is_stale, incoming_links, date,"
        " prompt_version, incoming_stems, status FROM note_index ORDER BY stem"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        r = list(r)
        r[1] = Path(r[1]).relative_to(vault).as_posix()
        out.append(tuple(r))
    return out


def _fixed_note(vault: Path, rel: str, body: str, mtime: float) -> None:
    p = _note(vault, rel, body)
    os.utime(p, (mtime, mtime))


def _seed(vault: Path) -> None:
    t = 1_700_000_000.0
    _fixed_note(vault, "Patterns/a.md", _fm(["b", "c"], "A"), t)
    _fixed_note(vault, "Patterns/b.md", _fm(["c"], "B"), t)
    _fixed_note(vault, "Patterns/c.md", _fm([], "C"), t)
    _fixed_note(vault, "Debugging/d.md", _fm(["a", "ghost"], "D"), t)
    _fixed_note(vault, "Debugging/e.md", _fm(["a"], "E"), t)
    _fixed_note(vault, "Patterns/f.md", _fm(["f"], "F"), t)
    _fixed_note(vault, "A/dup.md", _fm(["a"], "dup in A"), t)
    _fixed_note(vault, "B/dup.md", _fm(["b"], "dup in B"), t)


def _mutate(vault: Path) -> None:
    t = 1_700_000_500.0
    _fixed_note(vault, "Patterns/b.md", _fm(["c"], "B edited"), t)  # edit
    _fixed_note(vault, "Patterns/g.md", _fm(["a", "d"], "G"), t)  # add
    (vault / "Debugging" / "e.md").unlink()  # delete
    (vault / "Patterns" / "c.md").rename(vault / "Patterns" / "c2.md")  # rename
    _fixed_note(vault, "Patterns/a.md", _fm(["b", "c2", "g"], "A"), t)  # +/- links
    _fixed_note(vault, "Debugging/d.md", _fm(["ghost"], "D"), t)  # link removed
    _fixed_note(vault, "Patterns/new.md", _fm(["ghost2"], "N"), t)  # dangling
    _fixed_note(vault, "Patterns/ghost.md", _fm([], "ghost now exists"), t)
    _fixed_note(
        vault, "A/dup.md", _fm(["a", "g"], "dup in A edited"), t
    )  # loser/winner edit


def test_equivalence_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    import update_index as ui
    from parsidion.cli.index.build import build_index
    from parsidion.core.note_index import ensure_note_index_schema

    inc, full = tmp_path / "inc", tmp_path / "full"
    for v in (inc, full):
        v.mkdir()
        _seed(v)
    assert _apply(inc) == 8
    for v in (inc, full):
        _mutate(v)
    _apply(inc)

    conn = _open(full)
    ensure_note_index_schema(conn)
    conn.close()
    *_, db_rows, _counter = build_index(vault=full)
    ui._write_note_index_to_db(db_rows, {r.stem for r in db_rows}, vault=full)

    assert _snapshot(inc) == _snapshot(full)
    assert not diff_changed_notes(inc)


# ---------------------------------------------------------------------------
# Task 3: fallback triggers + mode plumbing
# ---------------------------------------------------------------------------


def _apply_result(vault: Path):
    conn = _open(vault)
    try:
        return apply_incremental_update(vault, conn)
    finally:
        conn.close()


def _events(vault: Path) -> list[dict]:
    log = vault / "hook_events.log"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def test_corrupt_index_state_falls_back_to_full(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    conn = _open(tmp_path)
    conn.execute("UPDATE index_state SET sha256 = NULL, mtime = 'junk'")
    conn.commit()
    conn.close()
    res = _apply_result(tmp_path)
    assert res.fallback_reason == "index_state_corrupt"
    assert _row(tmp_path, "b") == (1, ["a"])
    assert not diff_changed_notes(tmp_path)  # reseeded


def test_missing_index_state_falls_back_to_full(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    conn = _open(tmp_path)
    conn.execute("DELETE FROM index_state")
    conn.commit()
    conn.close()
    assert _apply_result(tmp_path).fallback_reason == "index_state_missing"
    assert not diff_changed_notes(tmp_path)


def test_exclude_dirs_change_falls_back(tmp_path: Path, monkeypatch) -> None:
    import parsidion.core.note_index as ni

    _base_vault(tmp_path)
    monkeypatch.setattr(ni, "EXCLUDE_DIRS", set(ni.EXCLUDE_DIRS) | {"Patterns"})
    res = _apply_result(tmp_path)
    assert res.fallback_reason == "exclude_dirs_changed"
    # reseeded under the new marker: next run is incremental again
    assert _apply_result(tmp_path).fallback_reason is None


def test_schema_marker_mismatch_falls_back(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    conn = _open(tmp_path)
    conn.execute("UPDATE index_state_meta SET value = 'old' WHERE key = 'schema'")
    conn.commit()
    conn.close()
    assert _apply_result(tmp_path).fallback_reason == "schema_marker_mismatch"


def test_fallback_reason_logged(tmp_path: Path) -> None:
    _base_vault(tmp_path)
    conn = _open(tmp_path)
    conn.execute("UPDATE index_state SET sha256 = NULL")
    conn.commit()
    conn.close()
    _apply_result(tmp_path)
    fallbacks = [e for e in _events(tmp_path) if e["hook"] == "IndexFallback"]
    assert fallbacks and fallbacks[-1]["reason"] == "index_state_corrupt"


def test_full_flag_forces_rebuild(monkeypatch) -> None:
    import pytest

    from parsidion.cli.index.cli import _parse_args

    monkeypatch.setattr(sys, "argv", ["update_index.py", "--full"])
    assert _parse_args().full is True
    monkeypatch.setattr(sys, "argv", ["update_index.py", "--incremental"])
    args = _parse_args()
    assert args.incremental is True and args.full is False
    monkeypatch.setattr(sys, "argv", ["update_index.py"])
    args = _parse_args()
    assert args.incremental is False and args.full is False
    monkeypatch.setattr(sys, "argv", ["update_index.py", "--full", "--incremental"])
    with pytest.raises(SystemExit):
        _parse_args()


def test_run_index_rebuild_passes_mode(tmp_path: Path, monkeypatch) -> None:
    import parsidion.core.vault_index as vi

    seen: list[list[str]] = []

    def fake(argv, **kw):
        seen.append(argv)
        return "ok", None

    monkeypatch.setattr(vi, "run_with_pgkill", fake)
    vi.run_index_rebuild(tmp_path, mode="full")
    vi.run_index_rebuild(tmp_path, mode="incremental")
    vi.run_index_rebuild(tmp_path)
    assert "--full" in seen[0] and "--incremental" in seen[1]
    assert "--full" not in seen[2] and "--incremental" not in seen[2]


def test_tampered_index_state_key_skipped_safely(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text(_fm([], "Outside"), encoding="utf-8")
    _base_vault(vault)
    conn = _open(vault)
    for key in ("../outside.md", str(outside)):
        conn.execute(
            "INSERT OR REPLACE INTO index_state VALUES (?, 1.0, 'x', 0.0)", (key,)
        )
    conn.execute(
        "INSERT OR REPLACE INTO note_index (stem, path) VALUES ('outside', ?)",
        (str(outside),),
    )
    conn.commit()
    conn.close()
    before = outside.read_bytes()
    res = _apply_result(vault)
    assert res.fallback_reason is None
    assert outside.read_bytes() == before
    assert _row(vault, "b") == (1, ["a"])


def _run_main(vault: Path, monkeypatch, flag: str | None, config_value: bool):
    """Run update_index.main() with the DB writers stubbed; return the calls."""
    import update_index as ui
    from parsidion.core import vault_config

    (vault / "config.yaml").write_text(
        f"search:\n  incremental_note_index: {str(config_value).lower()}\n",
        encoding="utf-8",
    )
    vault_config.clear_config_cache()
    calls: list[str] = []
    monkeypatch.setattr(
        ui, "_incremental_note_index", lambda *a, **k: calls.append("incremental")
    )
    monkeypatch.setattr(
        ui, "_reseed_index_state", lambda *a, **k: calls.append("reseed")
    )
    monkeypatch.setattr(
        ui,
        "_write_note_index_to_db",
        lambda *a, **k: calls.append("full_write") or True,
    )
    monkeypatch.setenv("PARSIDION_STATIC_ONLY", "1")
    argv = ["update_index.py", "--vault", str(vault)]
    if flag:
        argv.append(flag)
    monkeypatch.setattr(sys, "argv", argv)
    ui.main()
    return calls


def _main_vault(vault: Path) -> Path:
    _note(vault, "Patterns/a.md", _fm([], "A"))
    sqlite3.connect(str(vault / "embeddings.db")).close()
    return vault


def test_main_full_flag_beats_config_true(tmp_vault: Path, monkeypatch) -> None:
    calls = _run_main(_main_vault(tmp_vault), monkeypatch, "--full", True)
    assert calls == ["full_write", "reseed"]


def test_main_incremental_flag_beats_config_false(tmp_vault: Path, monkeypatch) -> None:
    calls = _run_main(_main_vault(tmp_vault), monkeypatch, "--incremental", False)
    assert calls == ["incremental"]


def test_main_no_flag_config_false_is_full(tmp_vault: Path, monkeypatch) -> None:
    calls = _run_main(_main_vault(tmp_vault), monkeypatch, None, False)
    assert calls == ["full_write", "reseed"]


def test_main_no_flag_config_true_is_incremental(tmp_vault: Path, monkeypatch) -> None:
    calls = _run_main(_main_vault(tmp_vault), monkeypatch, None, True)
    assert calls == ["incremental"]


def test_tampered_rows_unchanged_and_keys_unsafe(tmp_path: Path) -> None:
    from parsidion.core.note_index import _safe_state_key

    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text(_fm([], "Outside"), encoding="utf-8")
    _base_vault(vault)
    keys = ("../outside.md", str(outside))
    assert all(_safe_state_key(vault, Path(k)) is None for k in keys)
    conn = _open(vault)
    for key in keys:
        conn.execute(
            "INSERT OR REPLACE INTO index_state VALUES (?, 1.0, 'x', 0.0)", (key,)
        )
    conn.execute(
        "INSERT OR REPLACE INTO note_index (stem, path, title)"
        " VALUES ('outside', ?, 'orig')",
        (str(outside),),
    )
    conn.commit()
    conn.close()
    _apply_result(vault)
    conn = _open(vault)
    state = dict(
        conn.execute("SELECT path, sha256 FROM index_state WHERE sha256 = 'x'")
    )
    (title,) = conn.execute(
        "SELECT title FROM note_index WHERE stem = 'outside'"
    ).fetchone()
    conn.close()
    assert state == {k: "x" for k in keys}
    assert title == "orig"


def test_rebuild_graph_targets_named_vault(tmp_path, monkeypatch) -> None:
    """build_graph must be invoked with the named vault, not its default."""
    from parsidion.cli.index import graph as graph_mod

    vault = tmp_path / "personal"
    vault.mkdir()
    cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        cmds.append(list(cmd))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(graph_mod.subprocess, "run", fake_run)
    graph_mod._rebuild_graph(include_daily=True, incremental=False, vault_path=vault)
    assert len(cmds) == 1
    assert cmds[0][cmds[0].index("--vault") + 1] == str(vault)


def test_main_rebuild_graph_passes_resolved_vault(tmp_vault, monkeypatch) -> None:
    import update_index as ui

    vault = _main_vault(tmp_vault)
    seen: dict = {}
    monkeypatch.setattr(ui, "_rebuild_graph", lambda **kw: seen.update(kw))
    for name in (
        "_incremental_note_index",
        "_reseed_index_state",
        "_write_note_index_to_db",
    ):
        monkeypatch.setattr(ui, name, lambda *a, **k: None)
    monkeypatch.setenv("PARSIDION_STATIC_ONLY", "1")
    monkeypatch.setattr(
        sys, "argv", ["update_index.py", "--vault", str(vault), "--rebuild-graph"]
    )
    ui.main()
    assert Path(seen["vault_path"]).resolve() == vault.resolve()


def _full_rebuild_snapshot(vault: Path) -> list[tuple]:
    import update_index as ui
    from parsidion.cli.index.build import build_index
    from parsidion.core.note_index import ensure_note_index_schema

    conn = _open(vault)
    ensure_note_index_schema(conn)
    conn.close()
    *_, db_rows, _counter = build_index(vault=vault)
    ui._write_note_index_to_db(db_rows, {r.stem for r in db_rows}, vault=vault)
    return _snapshot(vault)


def _dup_vault(root: Path, name: str) -> Path:
    v = root / name
    v.mkdir()
    t = 1_700_000_000.0
    _fixed_note(v, "A/dup.md", _fm(["x"], "dup A"), t)
    _fixed_note(v, "B/dup.md", _fm(["y"], "dup B"), t)
    _fixed_note(v, "Patterns/x.md", _fm(["dup"], "X"), t)
    _fixed_note(v, "Patterns/y.md", _fm([], "Y"), t)
    return v


def _walk_winner_and_loser(vault: Path) -> tuple[Path, Path]:
    from parsidion.core.vault_index import _walk_vault_notes

    dups = [p for p in _walk_vault_notes(vault) if p.stem == "dup"]
    assert len(dups) == 2
    return dups[-1], dups[0]


def test_dup_stem_delete_of_winner_matches_full_rebuild(tmp_path: Path) -> None:
    inc, full = _dup_vault(tmp_path, "inc"), _dup_vault(tmp_path, "full")
    _apply(inc)
    winner, _ = _walk_winner_and_loser(inc)
    (inc / winner.relative_to(inc)).unlink()
    w_full, _ = _walk_winner_and_loser(full)
    w_full.unlink()
    assert _apply(inc) == 1
    assert _row(inc, "dup") is not None
    assert _snapshot(inc) == _full_rebuild_snapshot(full)
    assert _apply(inc) == 0


def test_dup_stem_edit_of_loser_matches_full_rebuild(tmp_path: Path) -> None:
    inc, full = _dup_vault(tmp_path, "inc"), _dup_vault(tmp_path, "full")
    _apply(inc)
    for v in (inc, full):
        _, loser = _walk_winner_and_loser(v)
        loser.write_text(_fm(["y", "x"], "loser edited"), encoding="utf-8")
        os.utime(loser, (1, 1_700_000_900.0))
    assert _apply(inc) == 1
    assert _snapshot(inc) == _full_rebuild_snapshot(full)


def test_failed_full_write_does_not_reseed_index_state(
    tmp_path: Path, monkeypatch
) -> None:
    import update_index as ui

    _base_vault(tmp_path)
    conn = _open(tmp_path)
    conn.execute("DELETE FROM index_state")  # corrupt state -> fallback trigger
    conn.commit()
    conn.close()
    monkeypatch.setattr(ui, "_write_note_index_to_db", lambda *a, **k: False)
    conn = _open(tmp_path)
    res = apply_incremental_update(tmp_path, conn)
    n = conn.execute("SELECT COUNT(*) FROM index_state").fetchone()[0]
    conn.close()
    assert res.fallback_reason is not None
    assert n == 0
