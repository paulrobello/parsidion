"""Tests for parmem_backend.find_code_raw / parmem_search result mapping (Task 3)."""

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

import parmem_backend  # noqa: E402
import vault_common  # noqa: E402

from tests.fake_parmem import FakeHealth, FakeParMem  # noqa: E402

# The exact embeddings-path result shape (vault_search.search()); parity is a
# hard requirement — par-mem results must carry exactly these keys.
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
    vault_common.load_config.cache_clear()
    parmem_backend.reset_parmem_cache()


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
    tmp_vault: Path, fake_parmem: FakeParMem, fake_parmem_health: FakeHealth
) -> FakeParMem:
    """Backend available (fake binary + health) with decay disabled by default."""
    _write_config(tmp_vault, "embeddings:\n  decay_enabled: false\n")
    return fake_parmem


class TestFindCodeRaw:
    def test_returns_results_verbatim_and_passes_limit_and_cwd(
        self, tmp_vault: Path, ready: FakeParMem
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
        result = parmem_backend.find_code_raw("where is foo", top_k=7, cwd=tmp_vault)
        assert result == items
        call = ready.wait_for_call("find-code")
        assert call["argv"] == ["find-code", "where is foo", "--json", "--limit", "7"]
        assert Path(str(call["cwd"])).resolve() == tmp_vault.resolve()

    def test_unavailable_backend_returns_none(
        self, tmp_vault: Path, fake_parmem: FakeParMem
    ) -> None:
        # No health fixture: autouse isolation points at an unreachable port.
        assert parmem_backend.find_code_raw("q") is None
        fake_parmem.assert_no_call("find-code", settle=0.1)

    def test_nonzero_exit_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        ready.configure(exit_code=1, stderr_output="repo not indexed: /some/path\n")
        assert parmem_backend.find_code_raw("q", cwd=tmp_vault) is None
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["hook"] == "ParMemBackend"
        assert event["detail"].startswith("exit:1")
        assert "repo not indexed" in event["detail"]  # stderr excerpt carried through

    def test_garbage_json_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        ready.configure(stdout_override="definitely { not json")
        assert parmem_backend.find_code_raw("q", cwd=tmp_vault) is None
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        assert "ParMemBackend" in log

    def test_missing_results_key_returns_none(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        # Guards the verified contract: a payload without a "results" list is
        # rejected — including the previously-guessed {"hits": []} shape.
        ready.configure(stdout_override='{"hits": []}')
        assert parmem_backend.find_code_raw("q", cwd=tmp_vault) is None
        ready.configure(stdout_override='{"unexpected": true}')
        assert parmem_backend.find_code_raw("q", cwd=tmp_vault) is None

    def test_timeout_returns_none_and_logs(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        _write_config(
            tmp_vault, "par_mem:\n  timeout_s: 1\nembeddings:\n  decay_enabled: false\n"
        )
        ready.configure(delay=3.0)
        start = time.monotonic()
        assert parmem_backend.find_code_raw("q", cwd=tmp_vault) is None
        assert time.monotonic() - start < 3.0  # killed, did not wait out the delay
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["detail"] == "timeout"


class TestParmemSearchMapping:
    def test_aggregates_per_note_enriches_and_sorts(
        self, tmp_vault: Path, ready: FakeParMem
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
        results = parmem_backend.parmem_search("q", top_k=10, vault=tmp_vault)
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
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        _make_note_index(tmp_vault)
        parmem_backend.parmem_search("q", top_k=10, vault=tmp_vault)
        call = ready.wait_for_call("find-code")
        argv = cast(list[str], call["argv"])
        assert argv[-2:] == ["--limit", "30"]

    def test_caps_at_top_k(self, tmp_vault: Path, ready: FakeParMem) -> None:
        _make_note_index(tmp_vault)
        items = []
        for i in range(5):
            _insert_note(tmp_vault, stem=f"note-{i}", mtime=1000.0 + i)
            items.append(
                {"file_path": f"Patterns/note-{i}.md", "score": 0.01 * (i + 1)}
            )
        ready.configure(find_code={"results": items})
        results = parmem_backend.parmem_search("q", top_k=2, vault=tmp_vault)
        assert results is not None
        assert len(results) == 2
        assert [r["stem"] for r in results] == ["note-4", "note-3"]

    def test_skips_hits_not_in_note_index(
        self, tmp_vault: Path, ready: FakeParMem
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
        results = parmem_backend.parmem_search("q", vault=tmp_vault)
        assert results is not None
        assert [r["stem"] for r in results] == ["real-note"]

    def test_skips_non_markdown_and_null_score_ranks_lowest(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        # Verified contract: `score` may be null — treat null as lowest, not
        # as a reason to drop the hit. Non-.md hits are still skipped.
        _make_note_index(tmp_vault)
        _insert_note(tmp_vault, stem="real-note")
        _insert_note(tmp_vault, stem="null-note")
        ready.configure(
            find_code={
                "results": [
                    {"file_path": "config.yaml", "score": 0.9},
                    {"file_path": "Patterns/null-note.md", "score": None},
                    {"file_path": "Patterns/real-note.md", "score": None},
                    {"file_path": "Patterns/real-note.md", "score": 0.04},
                ]
            }
        )
        results = parmem_backend.parmem_search("q", vault=tmp_vault)
        assert results is not None
        # Max aggregation: real-note's 0.04 beats its own null (0.0); the
        # null-only note survives with score 0.0 and sorts last.
        assert [r["stem"] for r in results] == ["real-note", "null-note"]
        assert results[0]["score"] == pytest.approx(0.04)
        assert results[1]["score"] == pytest.approx(0.0)

    def test_empty_results_returns_empty_list(
        self, tmp_vault: Path, ready: FakeParMem
    ) -> None:
        results = parmem_backend.parmem_search("q", vault=tmp_vault)
        assert results == []

    def test_applies_temporal_decay_matching_vault_search(
        self, tmp_vault: Path, ready: FakeParMem
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
        results = parmem_backend.parmem_search("q", vault=tmp_vault)
        assert results is not None
        expected = vault_search._apply_decay(0.08, old_mtime, time.time())
        score = cast(float, results[0]["score"])
        assert float(score) == pytest.approx(expected, rel=1e-3)
        assert float(score) < 0.08

    def test_fallback_mapping_without_note_index(
        self, tmp_vault: Path, ready: FakeParMem
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
        results = parmem_backend.parmem_search("q", vault=tmp_vault)
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
        self, tmp_vault: Path, ready: FakeParMem, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr(parmem_backend, "_run_parmem", boom)
        assert parmem_backend.parmem_search("q", vault=tmp_vault) is None
