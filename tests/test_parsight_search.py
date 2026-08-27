"""Tests for parsight_backend.find_code_raw / parsight_search result mapping (Task 3)."""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import cast

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core import parsight_backend  # noqa: E402 — ARC-006: patch internals where they live
import vault_common  # noqa: E402

from tests.fake_parsight import FakeHealth, FakeParsight  # noqa: E402

# The exact embeddings-path result shape (vault_search.search()); parity is a
# hard requirement — parsight results must carry exactly these keys.
EXPECTED_KEYS = {
    "score",
    "stem",
    "title",
    "folder",
    "tags",
    "path",
    "summary",
    "note_type",
    "project",
    "confidence",
    "mtime",
    "related",
    "is_stale",
    "incoming_links",
}


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.clear_config_cache()
    parsight_backend.reset_parsight_cache()


def _make_note_index(vault: Path) -> None:
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    conn.execute(
        """
        CREATE TABLE note_index (
            stem TEXT PRIMARY KEY, path TEXT, folder TEXT, title TEXT,
            summary TEXT, tags TEXT, note_type TEXT, project TEXT,
            confidence TEXT, mtime REAL, related TEXT,
            is_stale INTEGER DEFAULT 0, incoming_links INTEGER DEFAULT 0,
            date TEXT DEFAULT ''
        )
        """
    )
    conn.commit()
    conn.close()


def _insert_note(
    vault: Path,
    *,
    stem: str,
    folder: str = "Patterns",
    title: str = "",
    tags: str = "",
    mtime: float = 1000.0,
) -> Path:
    path = vault / folder / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title or stem}\nBody.\n", encoding="utf-8")
    conn = sqlite3.connect(str(vault / "embeddings.db"))
    conn.execute(
        "INSERT INTO note_index (stem, path, folder, title, summary, tags, note_type,"
        " project, confidence, mtime, related, is_stale, incoming_links)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            stem,
            str(path),
            folder,
            title or stem,
            "A summary.",
            tags,
            "pattern",
            "proj-x",
            "high",
            mtime,
            "[[other-note]]",
            0,
            2,
        ),
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def ready(
    tmp_vault: Path, fake_parsight: FakeParsight, fake_parsight_health: FakeHealth
) -> FakeParsight:
    """Backend available (fake binary + health) with decay disabled by default."""
    _write_config(tmp_vault, "embeddings:\n  decay_enabled: false\n")
    return fake_parsight


class TestFindCodeRaw:
    def test_returns_results_verbatim_and_passes_limit_and_cwd(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Full verified MCP find_code item shape — returned verbatim.
        items = [
            {
                "file_path": "src/lib.rs",
                "start_line": 10,
                "end_line": 24,
                "name": "resolve_worktree",
                "kind": "function",
                "worktree_id": "wt-1",
                "id": "01HXYZ",
                "score": 0.031,
            }
        ]
        ready.configure(find_code={"results": items, "_meta": {"count": 1}})
        result = parsight_backend.find_code_raw("where is foo", top_k=7, cwd=tmp_vault)
        assert result == items
        call = ready.wait_for_call("find-code")
        assert call["argv"] == [
            "find-code",
            "where is foo",
            "--json",
            "--diagnostics",
            "--limit",
            "7",
        ]
        assert Path(str(call["cwd"])).resolve() == tmp_vault.resolve()

    def test_unavailable_backend_returns_none(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        # No health fixture: autouse isolation points at an unreachable port.
        assert parsight_backend.find_code_raw("q") is None
        fake_parsight.assert_no_call("find-code", settle=0.1)

    def test_nonzero_exit_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        ready.configure(exit_code=1, stderr_output="repo not indexed: /some/path\n")
        assert parsight_backend.find_code_raw("q", cwd=tmp_vault) is None
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["hook"] == "ParsightBackend"
        assert event["detail"].startswith("exit:1")
        assert "repo not indexed" in event["detail"]  # stderr excerpt carried through

    def test_garbage_json_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        ready.configure(stdout_override="definitely { not json")
        assert parsight_backend.find_code_raw("q", cwd=tmp_vault) is None
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        assert "ParsightBackend" in log

    def test_missing_results_key_returns_none(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Guards the verified contract: a payload without a "results" list is
        # rejected — including the previously-guessed {"hits": []} shape.
        ready.configure(stdout_override='{"hits": []}')
        assert parsight_backend.find_code_raw("q", cwd=tmp_vault) is None
        ready.configure(stdout_override='{"unexpected": true}')
        assert parsight_backend.find_code_raw("q", cwd=tmp_vault) is None

    def test_timeout_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        _write_config(
            tmp_vault,
            "parsight:\n  timeout_s: 1\nembeddings:\n  decay_enabled: false\n",
        )
        ready.configure(delay=3.0)
        start = time.monotonic()
        assert parsight_backend.find_code_raw("q", cwd=tmp_vault) is None
        assert time.monotonic() - start < 3.0  # killed, did not wait out the delay
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["detail"] == "timeout"


class TestParsightSearchMapping:
    def test_aggregates_per_note_enriches_and_sorts(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="note-a", title="Note A", tags="python, vault")
        _insert_note(tmp_vault, stem="note-b", folder="Debugging", title="Note B")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/note-a.md", "score": 0.05},
                    {"file_path": "Debugging/note-b.md", "score": 0.07},
                    {"file_path": "Patterns/note-a.md", "score": 0.09},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", top_k=10, vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["note-a", "note-b"]
        top = results[0]
        assert set(top.keys()) == EXPECTED_KEYS
        assert top["score"] == pytest.approx(0.09)  # max across heading hits
        assert top["title"] == "Note A"
        assert top["folder"] == "Patterns"
        assert top["tags"] == ["python", "vault"]
        assert top["path"] == str(tmp_vault / "Patterns" / "note-a.md")
        assert top["summary"] == "A summary."
        assert top["note_type"] == "pattern"
        assert top["project"] == "proj-x"
        assert top["confidence"] == "high"
        assert top["mtime"] == pytest.approx(1000.0)
        assert top["related"] == ["[[other-note]]"]
        assert top["is_stale"] is False
        assert top["incoming_links"] == 2

    def test_overfetches_three_times_top_k(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        _make_note_index(tmp_vault)
        parsight_backend.parsight_search("q", top_k=10, vault=tmp_vault)
        call = ready.wait_for_call("find-code")
        argv = cast(list[str], call["argv"])
        assert argv[-2:] == ["--limit", "30"]

    def test_overfetch_clamped_to_server_limit(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # find-code's server-side --limit ceiling is 1000; a large caller
        # top_k must not produce a rejected over-fetch request.
        _make_note_index(tmp_vault)
        parsight_backend.parsight_search("q", top_k=500, vault=tmp_vault)
        call = ready.wait_for_call("find-code")
        argv = cast(list[str], call["argv"])
        assert argv[-2:] == ["--limit", "1000"]

    def test_caps_at_top_k(self, tmp_vault: Path, ready: FakeParsight) -> None:
        _make_note_index(tmp_vault)
        items = []
        for i in range(5):
            _insert_note(tmp_vault, stem=f"note-{i}", mtime=1000.0 + i)
            items.append(
                {"file_path": f"Patterns/note-{i}.md", "score": 0.01 * (i + 1)}
            )
        ready.configure(find_code={"results": items})
        results = parsight_backend.parsight_search("q", top_k=2, vault=tmp_vault)
        assert results is not None
        assert len(results) == 2
        assert [r["stem"] for r in results] == ["note-4", "note-3"]

    def test_skips_hits_not_in_note_index(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="real-note")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/real-note.md", "score": 0.05},
                    {"file_path": "Patterns/MANIFEST.md", "score": 0.9},
                    {"file_path": "CLAUDE.md", "score": 0.8},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["real-note"]

    def test_skips_non_markdown_and_null_score_gets_rank_preserving_fallback(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Verified contract: `score` may be null — a null score is NOT
        # floored to 0.0 (that would let any note with even a tiny real
        # score leapfrog a note parsight actually ranked higher but returned
        # without diagnostics). Instead it gets a synthetic value from its
        # position in parsight's response (earlier position, higher value).
        # Non-.md hits are still skipped, and a real score wins outright
        # when it beats every synthetic value in play.
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="scored-note")
        _insert_note(tmp_vault, stem="first-note")
        _insert_note(tmp_vault, stem="second-note")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "config.yaml", "score": 0.9},  # not .md, skipped
                    {"file_path": "Patterns/scored-note.md", "score": 0.6},
                    {"file_path": "Patterns/first-note.md", "score": None},
                    {"file_path": "Patterns/second-note.md", "score": None},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == [
            "scored-note",
            "first-note",
            "second-note",
        ]
        assert results[0]["score"] == pytest.approx(0.6)
        assert results[1]["score"] == pytest.approx(1 / 3, abs=1e-4)  # idx 2: 1/(1+2)
        assert results[2]["score"] == pytest.approx(0.25)  # idx 3: 1/(1+3)

    def test_rank_preserving_fallback_beats_null_floor_to_zero(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Regression for the live-verified bug: flooring a null score to
        # 0.0 let ANY note with a nonzero real score outrank a note parsight
        # actually ranked first but returned with no score — inverting
        # relevance order. Crafted mtimes rule out decay/recency as an
        # alternative explanation for the (correct) result order.
        _write_config(tmp_vault, "")  # decay enabled (default)
        _make_note_index(tmp_vault)
        now = time.time()
        _insert_note(tmp_vault, stem="top-note", mtime=now - 1 * 86400.0)
        _insert_note(tmp_vault, stem="weak-note", mtime=now - 400 * 86400.0)
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/top-note.md", "score": None},
                    {"file_path": "Patterns/weak-note.md", "score": 0.001},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["top-note", "weak-note"]

    def test_empty_results_returns_empty_list(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results == []

    def test_decay_config_resolved_once_per_search(
        self, tmp_vault: Path, ready: FakeParsight, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PRF-103: decay params are hoisted out of the scoring loop."""
        _write_config(tmp_vault, "")  # decay enabled (default)
        _make_note_index(tmp_vault)
        now = time.time()
        _insert_note(tmp_vault, stem="note-a", mtime=now - 10 * 86400.0)
        _insert_note(tmp_vault, stem="note-b", mtime=now - 20 * 86400.0)
        _insert_note(tmp_vault, stem="note-c", mtime=now - 30 * 86400.0)
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/note-a.md", "score": 0.05},
                    {"file_path": "Patterns/note-b.md", "score": 0.06},
                    {"file_path": "Patterns/note-c.md", "score": 0.07},
                ]
            }
        )
        resolve_calls: list[object] = []
        orig_resolve = parsight_backend.resolve_decay_params

        def counting_resolve(vault=None):
            resolve_calls.append(vault)
            return orig_resolve(vault)

        monkeypatch.setattr(parsight_backend, "resolve_decay_params", counting_resolve)
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        assert len(results) == 3
        # Once for the whole search — not once per scored row.
        assert len(resolve_calls) == 1

    def test_applies_temporal_decay_matching_vault_search(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        import vault_search

        _write_config(tmp_vault, "")  # decay enabled (default)
        _make_note_index(tmp_vault)
        old_mtime = time.time() - 90 * 86400.0  # one half-life old
        _insert_note(tmp_vault, stem="old-note", mtime=old_mtime)
        ready.configure(
            find_code={
                "results": [{"file_path": "Patterns/old-note.md", "score": 0.08}]
            }
        )
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        expected = vault_search._apply_decay(0.08, old_mtime, time.time())
        score = cast(float, results[0]["score"])
        assert float(score) == pytest.approx(expected, rel=1e-3)
        assert float(score) < 0.08

    def test_aggregation_with_decay_applies_once_to_aggregated_max(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Pins current behavior: decay must be applied ONCE, to the
        # already-aggregated per-note max score — not per hit before
        # aggregation. note-a has two heading-section hits (0.05, 0.09); its
        # decayed score must equal decay(max=0.09), not e.g. a decayed sum
        # or a decayed average of both hits. note-b has a single hit and a
        # different mtime so the two notes' decay factors differ.
        import vault_search

        _write_config(tmp_vault, "")  # decay enabled (default)
        _make_note_index(tmp_vault)
        now = time.time()
        mtime_a = now - 90 * 86400.0  # exactly one half-life old
        mtime_b = now - 30 * 86400.0
        _insert_note(tmp_vault, stem="note-a", title="Note A", mtime=mtime_a)
        _insert_note(
            tmp_vault, stem="note-b", folder="Debugging", title="Note B", mtime=mtime_b
        )
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/note-a.md", "score": 0.05},
                    {"file_path": "Debugging/note-b.md", "score": 0.07},
                    {"file_path": "Patterns/note-a.md", "score": 0.09},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", top_k=10, vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["note-a", "note-b"]
        expected_a = vault_search._apply_decay(0.09, mtime_a, time.time())
        expected_b = vault_search._apply_decay(0.07, mtime_b, time.time())
        score_a = cast(float, results[0]["score"])
        score_b = cast(float, results[1]["score"])
        assert float(score_a) == pytest.approx(expected_a, rel=1e-3)
        assert float(score_b) == pytest.approx(expected_b, rel=1e-3)
        # Not decayed per-hit-then-summed (0.05 and 0.09 each decayed and
        # added) — the aggregated max alone drives the decayed score.
        wrong_summed = vault_search._apply_decay(
            0.05, mtime_a, time.time()
        ) + vault_search._apply_decay(0.09, mtime_a, time.time())
        assert float(score_a) != pytest.approx(wrong_summed, rel=1e-3)

    def test_fallback_mapping_without_note_index(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # No embeddings.db at all (embeddings-disabled vault): map from files,
        # excluding generated index files, with the same 14-key shape.
        note = tmp_vault / "Patterns" / "loose-note.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# Loose Note\nBody.\n", encoding="utf-8")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/loose-note.md", "score": 0.06},
                    {"file_path": "CLAUDE.md", "score": 0.9},
                    {"file_path": "Patterns/missing.md", "score": 0.5},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", vault=tmp_vault)
        assert results is not None
        assert len(results) == 1
        entry = results[0]
        assert set(entry.keys()) == EXPECTED_KEYS
        assert entry["stem"] == "loose-note"
        assert entry["title"] == "Loose Note"  # stem.replace("-", " ").title()
        assert entry["path"] == str(note)
        assert entry["tags"] == []
        assert isinstance(entry["mtime"], float)

    def test_never_raises_when_runner_explodes(
        self, tmp_vault: Path, ready: FakeParsight, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr(parsight_backend, "_run_parsight", boom)
        assert parsight_backend.parsight_search("q", vault=tmp_vault) is None

    def test_sec020_hits_outside_vault_are_skipped(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        """SEC-020: parsight file_path values are external input.

        An absolute path outside the vault (or a ../-laden relative) must be
        dropped before it reaches the result set or a file read.
        """
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="note-a", title="Note A")
        outside = tmp_vault.parent / "outside-secret.md"
        outside.write_text("# secret\n", encoding="utf-8")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "Patterns/note-a.md", "score": 0.9},
                    {"file_path": str(outside), "score": 0.95},
                    {"file_path": "../../../etc/passwd.md", "score": 0.99},
                ]
            }
        )
        results = parsight_backend.parsight_search("q", top_k=10, vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["note-a"]

    def test_sec020_tampered_note_index_row_path_is_skipped(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        """SEC-020: note_index rows are DB-sourced; an injected outside
        path must not flow into results via the enrichment row."""
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="note-a", title="Note A")
        conn = sqlite3.connect(str(tmp_vault / "embeddings.db"))
        conn.execute(
            "UPDATE note_index SET path = ? WHERE stem = ?",
            (str(tmp_vault.parent / "evil.md"), "note-a"),
        )
        conn.commit()
        conn.close()
        ready.configure(
            find_code={"results": [{"file_path": "Patterns/note-a.md", "score": 0.9}]}
        )
        results = parsight_backend.parsight_search("q", top_k=10, vault=tmp_vault)
        assert results == []
