"""ENH-022: vec0 KNN index parity + fallback contract for embeddings search.

``_search_embeddings`` must prefer the sqlite-vec ``note_vec`` KNN index and
return exactly what the brute-force cosine scan returns — same stems, same
order, scores equal within float tolerance — with the ARC-102 decay →
min_score → sort → truncate pipeline untouched on either path. When
``note_vec`` is missing or row-count-out-of-sync, the exact scan serves the
query and the fallback is logged once per process.

The build side (``build_embeddings.ensure_note_vec_schema`` and the
dual-write helpers) is covered without loading fastembed: the mirror is
seeded through the same pure-SQL upgrade path a real legacy DB takes.

Runs only under the ``search`` extra (``pytest.importorskip("sqlite_vec")``);
the fastembed model is bypassed by stubbing ``_embed_query``.
"""

from __future__ import annotations

import math
import random
import struct
import sys
import time
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("sqlite_vec")

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_embeddings  # noqa: E402
from cli.search import embeddings as cli_embeddings  # noqa: E402
from vault_config import apply_decay_score  # noqa: E402

# Query vector [1, 0]: cosine(query, v) == v[0] for unit vectors.
_QUERY_VEC = [1.0, 0.0]
_NOTE_COUNT = 200
_TOP = 10


def _unit(x: float) -> list[float]:
    """Unit 2-dim vector with cosine(_QUERY_VEC, v) == x."""
    return [x, math.sqrt(max(0.0, 1.0 - x * x))]


def _blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return dot / norm


SeedRow = tuple[str, list[float], float]


def _insert_rows(vault: Path, rows: list[SeedRow]) -> None:
    """Insert raw note_embeddings rows (the state a legacy DB is in)."""
    conn = build_embeddings.open_embeddings_db(vault / "embeddings.db")
    with conn:
        for stem, vec, mtime in rows:
            conn.execute(
                "INSERT INTO note_embeddings (stem, path, folder, title, tags, "
                "embedding, mtime) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    stem,
                    f"/vault/{stem}.md",
                    "Patterns",
                    stem,
                    "vec0",
                    _blob(vec),
                    mtime,
                ),
            )
    conn.close()


def _seed_notes(vault: Path) -> list[SeedRow]:
    """Seed ~200 notes with deterministic pseudo-random 2-dim unit vectors."""
    rng = random.Random(20260827)
    now = time.time()
    rows: list[SeedRow] = []
    for i in range(_NOTE_COUNT):
        x = rng.uniform(-0.5, 0.999)
        rows.append((f"note-{i:03d}", _unit(x), now - rng.uniform(0, 300) * 86400.0))
    _insert_rows(vault, rows)
    # The real upgrade path: create + backfill the vec0 mirror from the
    # stored rows (pure SQL, no model load involved).
    conn = build_embeddings.open_embeddings_db(vault / "embeddings.db")
    build_embeddings.ensure_note_vec_schema(conn)
    conn.close()
    return rows


def _brute_force(
    rows: list[SeedRow], top: int, min_score: float, decay_enabled: bool
) -> list[dict[str, object]]:
    """Reference scan pipeline (ARC-102): raw-cosine over-fetch → decay →
    min_score → sort → truncate → round."""
    now = time.time() if decay_enabled else 0.0
    # ARC-102: candidates are the raw-cosine top (top*3) — decay can reorder
    # within that pool but never sees past it, on either DB path.
    candidates = sorted(rows, key=lambda r: _cosine(_QUERY_VEC, r[1]), reverse=True)[
        : top * 3
    ]
    scored: list[tuple[float, str]] = []
    for stem, vec, mtime in candidates:
        score = _cosine(_QUERY_VEC, vec)
        if decay_enabled and mtime:
            score = apply_decay_score(score, mtime, now)
        if score < min_score:
            continue
        scored.append((score, stem))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"stem": stem, "score": round(score, 4)} for score, stem in scored[:top]]


@pytest.fixture()
def vec_env(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vault whose embeddings search uses a fixed query vector."""
    monkeypatch.setattr(
        cli_embeddings,
        "_embed_query",
        lambda q, model, vault, backend=None: list(_QUERY_VEC),
    )
    monkeypatch.setattr(cli_embeddings, "_note_vec_fallback_logged", False)
    return tmp_vault


def _decay_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the embeddings module's decay_enabled config read at False."""
    real_get_config = cli_embeddings.get_config

    def _fake_get_config(section: str, key: str, default: object = None) -> object:
        if (section, key) == ("embeddings", "decay_enabled"):
            return False
        return real_get_config(section, key, default)

    monkeypatch.setattr(cli_embeddings, "get_config", _fake_get_config)


def _search(vault: Path, top: int = _TOP) -> list[dict[str, object]]:
    return cli_embeddings._search_embeddings("q", top=top, min_score=0.0, vault=vault)


def _assert_parity(
    results: list[dict[str, object]], expected: list[dict[str, object]]
) -> None:
    """Assert same stems in the same order, per-stem scores within 1e-5."""
    assert [r["stem"] for r in results] == [e["stem"] for e in expected]
    by_stem = {cast(str, e["stem"]): cast(float, e["score"]) for e in expected}
    for r in results:
        assert abs(cast(float, r["score"]) - by_stem[cast(str, r["stem"])]) <= 1e-5


class TestVec0Parity:
    def test_vec0_parity_top10_decay_on(self, vec_env: Path) -> None:
        """KNN top-10 equals brute force (stems, order, scores) with decay on."""
        rows = _seed_notes(vec_env)
        results = _search(vec_env)
        _assert_parity(results, _brute_force(rows, _TOP, 0.0, decay_enabled=True))

    def test_vec0_parity_top10_decay_off(
        self, vec_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same parity contract with decay disabled (raw cosine ordering)."""
        _decay_off(monkeypatch)
        rows = _seed_notes(vec_env)
        results = _search(vec_env)
        _assert_parity(results, _brute_force(rows, _TOP, 0.0, decay_enabled=False))

    def test_vec0_served_without_fallback_note(
        self, vec_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A synced mirror must serve via KNN — no fallback note emitted."""
        notes: list[str] = []
        monkeypatch.setattr(
            cli_embeddings,
            "_note_vec_fallback_note",
            lambda reason: notes.append(reason),
        )
        _seed_notes(vec_env)
        results = _search(vec_env)
        assert len(results) == _TOP
        assert notes == []


class TestVec0Fallback:
    def test_fallback_when_note_vec_dropped(
        self, vec_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Dropped note_vec: scan serves identical results, logs once."""
        rows = _seed_notes(vec_env)
        conn = build_embeddings.open_embeddings_db(vec_env / "embeddings.db")
        with conn:
            conn.execute("DROP TABLE note_vec")
        conn.close()

        results = _search(vec_env)
        _assert_parity(results, _brute_force(rows, _TOP, 0.0, decay_enabled=True))

        err = capsys.readouterr().err
        assert "exact scan fallback" in err
        # Logged once per process, not per query.
        _search(vec_env)
        assert capsys.readouterr().err.count("exact scan fallback") == 0

    def test_fallback_when_mirror_out_of_sync(
        self, vec_env: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Row-count desync (partial build / old binary write): scan serves."""
        rows = _seed_notes(vec_env)
        conn = build_embeddings.open_embeddings_db(vec_env / "embeddings.db")
        with conn:
            conn.execute("DELETE FROM note_vec WHERE rowid = 1")
        conn.close()

        results = _search(vec_env)
        expected = _brute_force(rows, _TOP, 0.0, decay_enabled=True)
        assert [r["stem"] for r in results] == [e["stem"] for e in expected]
        assert "exact scan fallback" in capsys.readouterr().err


class TestNoteVecSchema:
    def test_ensure_upgrades_legacy_db_and_stamps_version(
        self, tmp_vault: Path
    ) -> None:
        """A pre-ENH-022 DB gains a backfilled mirror + version marker."""
        now = time.time()
        rows = [(f"n{i}", _unit(0.5 + 0.01 * i), now - i * 86400.0) for i in range(5)]
        db = tmp_vault / "embeddings.db"
        # Single connection: a fresh open runs ensure BEFORE any rows exist
        # (no-op), so the DB is in its legacy state until ensure is invoked
        # explicitly below.
        conn = build_embeddings.open_embeddings_db(db)
        with conn:
            for stem, vec, mtime in rows:
                conn.execute(
                    "INSERT INTO note_embeddings (stem, path, embedding, mtime) "
                    "VALUES (?, ?, ?, ?)",
                    (stem, f"/vault/{stem}.md", _blob(vec), mtime),
                )
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'note_vec'"
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0

        build_embeddings.ensure_note_vec_schema(conn)
        mirrored = conn.execute(
            "SELECT nv.rowid FROM note_vec nv "
            "JOIN note_embeddings ne ON ne.id = nv.rowid"
        ).fetchall()
        assert len(mirrored) == len(rows)
        assert conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()[0] == len(rows)
        assert (
            conn.execute("PRAGMA user_version").fetchone()[0]
            == build_embeddings.EMBEDDINGS_SCHEMA_VERSION
        )
        conn.close()

        # Idempotent: a second ensure changes nothing.
        conn = build_embeddings.open_embeddings_db(db)
        assert conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()[0] == len(rows)
        conn.close()

    def test_ensure_recreates_dim_mismatched_mirror(self, tmp_vault: Path) -> None:
        """A mirror built for another dimension is rebuilt at the stored one."""
        now = time.time()
        rows = [(f"n{i}", _unit(0.5 + 0.01 * i), now) for i in range(3)]
        _insert_rows(tmp_vault, rows)
        db = tmp_vault / "embeddings.db"
        conn = build_embeddings.open_embeddings_db(db)
        with conn:
            # open_embeddings_db already created the (correct) mirror; swap
            # it for a wrong-dimension one to model a model-switch DB.
            conn.execute("DROP TABLE note_vec")
            conn.execute(
                "CREATE VIRTUAL TABLE note_vec USING "
                "vec0(embedding float[3] distance_metric=cosine)"
            )
            conn.execute(
                "INSERT INTO note_vec(rowid, embedding) VALUES (1, ?)",
                (struct.pack("3f", 0.5, 0.5, 0.7071),),
            )
        conn.close()

        conn = build_embeddings.open_embeddings_db(db)
        build_embeddings.ensure_note_vec_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()[0] == len(rows)
        conn.close()

    def test_sync_note_vec_stems_mirrors_upserts(self, tmp_vault: Path) -> None:
        """The dual-write helper re-points a stem's mirror row at its new vector."""
        now = time.time()
        rows = [(f"n{i}", _unit(0.5 + 0.01 * i), now) for i in range(3)]
        _insert_rows(tmp_vault, rows)
        db = tmp_vault / "embeddings.db"
        conn = build_embeddings.open_embeddings_db(db)
        build_embeddings.ensure_note_vec_schema(conn)

        new_vec = _unit(0.99)
        with conn:
            conn.execute(
                "UPDATE note_embeddings SET embedding = ? WHERE stem = 'n1'",
                (_blob(new_vec),),
            )
            build_embeddings._sync_note_vec_stems(conn, ["n1"])

        mirrored = conn.execute(
            "SELECT nv.rowid, nv.embedding FROM note_vec nv "
            "JOIN note_embeddings ne ON ne.id = nv.rowid AND ne.stem = 'n1'"
        ).fetchone()
        assert mirrored is not None
        assert list(struct.unpack("2f", mirrored[1])) == pytest.approx(new_vec)
        assert conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()[0] == len(rows)
        conn.close()

    def test_ensure_note_vec_for_write_dim_handling(self, tmp_vault: Path) -> None:
        """Write-side ensure: creates when absent, recreates on dim change."""
        db = tmp_vault / "embeddings.db"
        conn = build_embeddings.open_embeddings_db(db)
        build_embeddings._ensure_note_vec_for_write(conn, 2)
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'note_vec'"
            ).fetchone()
            is not None
        )
        conn.execute(
            "INSERT INTO note_vec(rowid, embedding) VALUES (1, ?)", (_blob(_unit(0.9)),)
        )
        conn.commit()

        # Wrong-dim table (empty after the drop) gets recreated at 3.
        build_embeddings._ensure_note_vec_for_write(conn, 3)
        conn.execute(
            "INSERT INTO note_vec(rowid, embedding) VALUES (1, ?)",
            (struct.pack("3f", 0.5, 0.5, 0.7071),),
        )
        assert conn.execute("SELECT COUNT(*) FROM note_vec").fetchone()[0] == 1
        conn.close()
