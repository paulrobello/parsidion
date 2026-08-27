"""ARC-102: embeddings decay-ordering contract (parity with parsight backend).

``_search_embeddings`` must order results by the DECAYED score, not the raw
cosine the SQL fetches by: over-fetch 3x, apply decay, filter by min_score,
sort descending, truncate — the same contract ``parsight_backend.parsight_search``
implements. These tests seed a real sqlite-vec DB with controlled cosines and
mtimes and pin the decayed ordering; the expected values are computed with the
shared ``apply_decay_score`` (the exact function the parsight path uses), which
makes each case a parity test between the two backends' post-decay ordering.

Runs only under the ``search`` extra (``pytest.importorskip("sqlite_vec")``);
the fastembed model itself is bypassed by stubbing ``_embed_query``.
"""

from __future__ import annotations

import math
import struct
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("sqlite_vec")

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from cli.search import embeddings as cli_embeddings  # noqa: E402
from vault_config import apply_decay_score  # noqa: E402

# Query vector [1, 0]: cosine(query, v) == v[0] for unit vectors, so each
# note's raw score is directly controlled by its first vector component.
_QUERY_VEC = [1.0, 0.0]


def _vec(x: float) -> bytes:
    """Pack a unit 2-dim vector with cosine(_QUERY_VEC, v) == x."""
    return struct.pack("2f", x, math.sqrt(max(0.0, 1.0 - x * x)))


def _seed_db(vault: Path, rows: list[tuple[str, float, float]]) -> None:
    """Create embeddings.db with (stem, raw_cosine, mtime) rows."""
    import build_embeddings

    db_path = vault / "embeddings.db"
    conn = build_embeddings.open_embeddings_db(db_path)
    with conn:
        for stem, raw, mtime in rows:
            conn.execute(
                "INSERT INTO note_embeddings (stem, path, embedding, mtime) "
                "VALUES (?, ?, ?, ?)",
                (stem, f"/vault/{stem}.md", _vec(raw), mtime),
            )
    conn.close()


@pytest.fixture()
def decay_env(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Vault whose embeddings search uses a fixed query vector."""
    monkeypatch.setattr(
        cli_embeddings, "_embed_query", lambda q, model, vault: list(_QUERY_VEC)
    )
    return tmp_vault


class TestDecayOrderingContract:
    def test_decay_reorders_raw_order(
        self, decay_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An old high-raw note must lose to a new lower-raw note."""
        now = time.time()
        old_days = 400.0
        _seed_db(
            decay_env,
            [
                ("old-note", 0.99, now - old_days * 86400.0),
                ("new-note", 0.95, now),
            ],
        )
        results = cli_embeddings._search_embeddings(
            "q", top=1, min_score=0.0, vault=decay_env
        )
        assert [r["stem"] for r in results] == ["new-note"]
        # Parity: the winner is whoever apply_decay_score (the function the
        # parsight path uses) ranks first for the same raw scores/mtimes.
        decayed_old = apply_decay_score(0.99, now - old_days * 86400.0, now)
        decayed_new = apply_decay_score(0.95, now, now)
        assert decayed_new > decayed_old
        assert results[0]["score"] == pytest.approx(decayed_new, abs=1e-3)

    def test_overfetch_surfaces_row_outside_raw_top(
        self, decay_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A row outside the raw-cosine top-N can win after decay."""
        now = time.time()
        # Raw order: a(0.99) > c(0.96) > b(0.95). A raw LIMIT 2 fetches
        # {a, c}; decayed order is c > b > a — b must still be returned.
        _seed_db(
            decay_env,
            [
                ("a-old", 0.99, now - 400 * 86400.0),
                ("c-new", 0.96, now),
                ("b-new", 0.95, now),
            ],
        )
        results = cli_embeddings._search_embeddings(
            "q", top=2, min_score=0.0, vault=decay_env
        )
        assert [r["stem"] for r in results] == ["c-new", "b-new"]

    def test_min_score_applies_post_decay(
        self, decay_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A raw score above min_score but decayed below it is filtered."""
        now = time.time()
        _seed_db(
            decay_env,
            [
                ("old-note", 0.99, now - 400 * 86400.0),
                ("new-note", 0.95, now),
            ],
        )
        results = cli_embeddings._search_embeddings(
            "q", top=5, min_score=0.9, vault=decay_env
        )
        assert [r["stem"] for r in results] == ["new-note"]

    def test_sorted_descending_on_decayed_score(
        self, decay_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple surviving rows come back sorted by decayed score."""
        now = time.time()
        ages = {"n1": 0.0, "n2": 30.0, "n3": 200.0}
        _seed_db(
            decay_env,
            [
                ("n1", 0.90, now - ages["n1"] * 86400.0),
                ("n2", 0.93, now - ages["n2"] * 86400.0),
                ("n3", 0.99, now - ages["n3"] * 86400.0),
            ],
        )
        results = cli_embeddings._search_embeddings(
            "q", top=3, min_score=0.0, vault=decay_env
        )
        expected = sorted(
            (
                (apply_decay_score(0.90, now - ages["n1"] * 86400.0, now), "n1"),
                (apply_decay_score(0.93, now - ages["n2"] * 86400.0, now), "n2"),
                (apply_decay_score(0.99, now - ages["n3"] * 86400.0, now), "n3"),
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        assert [r["stem"] for r in results] == [stem for _, stem in expected]
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_truncates_to_top(
        self, decay_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Over-fetched rows are truncated back to top after the sort."""
        now = time.time()
        _seed_db(
            decay_env,
            [(f"n{i}", 0.80 + 0.01 * i, now) for i in range(6)],
        )
        results = cli_embeddings._search_embeddings(
            "q", top=3, min_score=0.0, vault=decay_env
        )
        assert len(results) == 3
        assert [r["stem"] for r in results] == ["n5", "n4", "n3"]
