"""ENH-016: ``adaptive_context.decay_days`` time decay of usefulness scores.

Pins:

* ``effective_score`` / ``decay_factor`` half-life math: a note unused for
  ``2 * decay_days`` keeps a quarter of its raw score.
* A stale note ranks below a fresh note with half its raw score in the
  session-start usefulness rerank.
* ``decay_days: 0`` disables decay and reproduces the pre-change order.
* A hit resets the decay clock (use recovers).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_adaptive  # noqa: E402
from core import vault_adaptive as core_adaptive  # noqa: E402
from session_start import seed_selection  # noqa: E402
from session_start.seed_selection import _rank_by_usefulness  # noqa: E402


def _ts(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _record(hits: int, misses: int, last_hit: str | None) -> dict:
    return {"hits": hits, "misses": misses, "last_hit": last_hit}


class TestDecayFactor:
    def test_half_life_math(self) -> None:
        now = time.time()
        rec = _record(3, 0, _ts(10))
        assert vault_adaptive.decay_factor(rec, now, 10) == pytest.approx(0.5)
        # abs tolerance: _ts truncates to whole seconds, so the age is a
        # fraction of a second short of exactly two half-lives.
        assert vault_adaptive.decay_factor(rec, now, 5) == pytest.approx(0.25, abs=1e-3)

    def test_zero_or_missing_disables_decay(self) -> None:
        now = time.time()
        rec = _record(3, 0, _ts(365))
        assert vault_adaptive.decay_factor(rec, now, 0) == 1.0
        assert vault_adaptive.decay_factor(_record(3, 0, None), now, 30) == 1.0

    def test_future_or_garbage_timestamp_is_neutral(self) -> None:
        now = time.time()
        assert vault_adaptive.decay_factor(_record(1, 0, _ts(-5)), now, 30) == 1.0
        assert vault_adaptive.decay_factor(_record(1, 0, "not-a-date"), now, 30) == 1.0


class TestEffectiveScore:
    def test_decayed_ratio(self) -> None:
        now = time.time()
        # Raw ratio (4+1)/(4+2) = 0.8333; 2 half-lives → ~0.2083.
        rec = _record(4, 2, _ts(60))
        assert vault_adaptive.effective_score(rec, now, 30) == pytest.approx(
            (5 / 8) * 0.25
        )

    def test_no_history_is_neutral_half(self) -> None:
        now = time.time()
        assert vault_adaptive.effective_score({}, now, 30) == 0.5
        assert vault_adaptive.effective_score(_record(0, 0, None), now, 30) == 0.5


class TestRankByUsefulnessDecay:
    @pytest.fixture()
    def scores_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point the usefulness store at a tmp HOME and stub the config read.

        The path is patched on ``core.vault_adaptive`` — the module whose
        globals ``load_usefulness_scores`` actually resolves names in (the
        flat ``vault_adaptive`` shim only re-exports).
        """
        home = tmp_path / "home"
        (home / ".claude").mkdir(parents=True)
        store = home / ".claude" / "note_usefulness.json"
        monkeypatch.setattr(core_adaptive, "get_usefulness_path", lambda: store)
        # seed_selection imported get_config by name — patch it where it is used.
        monkeypatch.setattr(
            seed_selection, "get_config", lambda section, key, default=None: default
        )
        return store

    def _write(self, path: Path, scores: dict) -> None:
        path.write_text(json.dumps(scores), encoding="utf-8")

    def test_stale_note_ranks_below_fresher_note_with_half_the_raw_score(
        self, scores_file: Path, tmp_path: Path
    ) -> None:
        # decay_days defaults to 30 in the stubbed read.
        # stale: raw 0.8333, last hit 60 days ago → ×0.25 → 0.2083
        # fresh: raw 0.4167, last hit today → ×1.0 → 0.4167 > 0.2083
        self._write(
            scores_file,
            {
                "stale-note": _record(4, 2, _ts(60)),
                "fresh-note": _record(1, 2, _ts(0)),
            },
        )
        notes = [tmp_path / "stale-note.md", tmp_path / "fresh-note.md"]
        ranked = _rank_by_usefulness(notes)
        assert [p.stem for p in ranked] == ["fresh-note", "stale-note"]

    def test_decay_days_zero_reproduces_raw_order(
        self,
        scores_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            seed_selection,
            "get_config",
            lambda section, key, default=None: (
                0 if (section, key) == ("adaptive_context", "decay_days") else default
            ),
        )
        self._write(
            scores_file,
            {
                "stale-note": _record(4, 2, _ts(60)),
                "fresh-note": _record(1, 2, _ts(0)),
            },
        )
        notes = [tmp_path / "fresh-note.md", tmp_path / "stale-note.md"]
        # Raw ratios: 0.8333 vs 0.4167 — decay disabled keeps stale first.
        ranked = _rank_by_usefulness(notes)
        assert [p.stem for p in ranked] == ["stale-note", "fresh-note"]

    def test_decay_days_one_demotes_ten_day_old_note(
        self,
        scores_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Acceptance shape: decay_days=1 demotes a note last used 10 days
        ago below one used today (equal raw scores isolate the decay)."""
        monkeypatch.setattr(
            seed_selection,
            "get_config",
            lambda section, key, default=None: (
                1 if (section, key) == ("adaptive_context", "decay_days") else default
            ),
        )
        self._write(
            scores_file,
            {
                "old-note": _record(2, 2, _ts(10)),
                "today-note": _record(2, 2, _ts(0)),
            },
        )
        notes = [tmp_path / "old-note.md", tmp_path / "today-note.md"]
        ranked = _rank_by_usefulness(notes)
        assert [p.stem for p in ranked] == ["today-note", "old-note"]

    def test_hit_resets_decay_clock(
        self,
        scores_file: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            seed_selection,
            "get_config",
            lambda section, key, default=None: (
                10 if (section, key) == ("adaptive_context", "decay_days") else default
            ),
        )
        # Both notes carry the same raw ratio and a 100-day-old last_hit;
        # update_usefulness_scores refreshes only the used one's clock.
        self._write(
            scores_file,
            {
                "used-note": _record(2, 2, _ts(100)),
                "idle-note": _record(2, 2, _ts(100)),
            },
        )
        vault_adaptive.update_usefulness_scores(
            {"used-note"}, ["used-note", "idle-note"]
        )
        notes = [tmp_path / "idle-note.md", tmp_path / "used-note.md"]
        ranked = _rank_by_usefulness(notes)
        assert [p.stem for p in ranked] == ["used-note", "idle-note"]
