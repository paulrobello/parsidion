"""Tests for atomic-write and locking fixes.

Covers:
- ``vault_adaptive``: locked read-modify-write + tmp/replace persistence
- ``vault_fs.migrate_pending_paths``: locked rewrite of the pending queue
- ``vault_fs.append_to_pending``: dedup still works after the lock rework
- ``vault_fs.git_commit_vault``: vault-root config.yaml excluded from auto-commits
- ``vault_hooks.write_hook_event``: atomic log rotation
- ``build_graph.write_graph_json``: tmp + replace (fake numpy injected)
- ``build_embeddings.full_rebuild``: delete + insert in one transaction after
  model load (fake fastembed / sqlite_vec injected)
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

import vault_adaptive
import vault_common
import vault_fs
import vault_hooks


def _boom_replace(self: Path, target: object) -> None:
    raise OSError("simulated replace failure")


# ---------------------------------------------------------------------------
# vault_adaptive — locked, atomic JSON persistence
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a fresh directory with an empty ~/.claude."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    return home


class TestVaultAdaptive:
    def test_last_seen_roundtrip(self, fake_home: Path) -> None:
        vault_adaptive.save_last_seen("myproj", ts="2026-01-01T00:00:00")
        assert vault_adaptive.load_last_seen()["myproj"] == "2026-01-01T00:00:00"
        # No stray tmp file left behind
        leftovers = [p.name for p in (fake_home / ".claude").iterdir()]
        assert not any(name.endswith(".tmp") for name in leftovers)

    def test_injected_notes_roundtrip(self, fake_home: Path) -> None:
        vault_adaptive.save_injected_notes("myproj", ["note-a", "note-b"])
        assert vault_adaptive.get_injected_stems("myproj") == ["note-a", "note-b"]

    def test_usefulness_scores_accumulate(self, fake_home: Path) -> None:
        vault_adaptive.update_usefulness_scores({"note-a"}, ["note-a", "note-b"])
        vault_adaptive.update_usefulness_scores({"note-a"}, ["note-a", "note-b"])
        scores = vault_adaptive.load_usefulness_scores()
        assert scores["note-a"]["hits"] == 2
        assert scores["note-b"]["misses"] == 2
        assert scores["note-a"]["last_hit"] is not None

    def test_replace_failure_is_nonfatal_and_preserves_file(
        self, fake_home: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        vault_adaptive.save_last_seen("proj1", ts="2026-01-01T00:00:00")
        original = vault_adaptive.load_last_seen()
        monkeypatch.setattr(Path, "replace", _boom_replace)
        vault_adaptive.save_last_seen("proj2")  # must not raise
        assert vault_adaptive.load_last_seen() == original

    def test_lock_failure_is_nonfatal_preserves_state(self, fake_home: Path) -> None:
        """A read-only ~/.claude must not raise, must leave the existing
        state file byte-identical, and must leave no .tmp residue from the
        aborted atomic write."""
        # Pre-seed known state so we can verify it survives the failed write.
        vault_adaptive.save_last_seen("proj-seed", ts="2026-01-01T00:00:00")
        last_seen_path = vault_adaptive.get_last_seen_path()
        original = last_seen_path.read_text(encoding="utf-8")

        claude_dir = fake_home / ".claude"
        claude_dir.chmod(0o500)
        try:
            vault_adaptive.save_last_seen("proj")  # must not raise
            vault_adaptive.update_usefulness_scores({"a"}, ["a"])  # must not raise
        finally:
            claude_dir.chmod(0o700)

        # The aborted write did not corrupt the existing state file.
        assert last_seen_path.read_text(encoding="utf-8") == original
        # No .tmp residue left behind by the failed atomic write.
        leftovers = [p.name for p in claude_dir.iterdir()]
        assert not any(name.endswith(".tmp") for name in leftovers)


# ---------------------------------------------------------------------------
# vault_fs — pending queue
# ---------------------------------------------------------------------------


class TestMigratePendingPaths:
    def _seed(self, tmp_vault: Path) -> tuple[Path, Path]:
        """Create a pending queue with one broken and one valid entry."""
        tdir = tmp_vault / "transcripts"
        tdir.mkdir()
        real = tdir / "agent-abc.jsonl"
        real.write_text("{}\n", encoding="utf-8")
        pending = tmp_vault / "pending_summaries.jsonl"
        broken = {"session_id": "abc", "transcript_path": str(tdir / "abc.jsonl")}
        valid = {"session_id": "keep", "transcript_path": str(real)}
        pending.write_text(
            json.dumps(broken) + "\n" + json.dumps(valid) + "\n", encoding="utf-8"
        )
        return pending, real

    def test_fixes_agent_prefix_and_rewrites_atomically(self, tmp_vault: Path) -> None:
        pending, real = self._seed(tmp_vault)
        fixed = vault_fs.migrate_pending_paths()
        assert fixed == 1
        lines = [json.loads(line) for line in pending.read_text().splitlines() if line]
        assert lines[0]["transcript_path"] == str(real)
        assert lines[1]["session_id"] == "keep"
        assert not pending.with_suffix(".jsonl.tmp").exists()

    def test_dry_run_reports_without_writing(self, tmp_vault: Path) -> None:
        pending, _real = self._seed(tmp_vault)
        original = pending.read_text()
        fixed = vault_fs.migrate_pending_paths(dry_run=True)
        assert fixed == 1
        assert pending.read_text() == original

    def test_replace_failure_leaves_original_intact(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pending, _real = self._seed(tmp_vault)
        original = pending.read_text()
        monkeypatch.setattr(Path, "replace", _boom_replace)
        with pytest.raises(OSError):
            vault_fs.migrate_pending_paths()
        assert pending.read_text() == original

    def test_missing_queue_returns_zero(self, tmp_vault: Path) -> None:
        assert vault_fs.migrate_pending_paths() == 0


class TestAppendToPending:
    def test_append_and_dedup(self, tmp_vault: Path) -> None:
        transcript = tmp_vault / "session-1.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        vault_fs.append_to_pending(transcript, "proj", {}, force=True)
        vault_fs.append_to_pending(transcript, "proj", {}, force=True)
        pending = tmp_vault / "pending_summaries.jsonl"
        lines = [line for line in pending.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["session_id"] == "session-1"


# ---------------------------------------------------------------------------
# vault_fs — git_commit_vault config.yaml exclusion (security)
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _git_ls_files(cwd: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )
    return {line for line in result.stdout.splitlines() if line}


@pytest.fixture()
def git_repo(tmp_vault: Path) -> Path:
    repo = tmp_vault / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    return repo


class TestGitCommitVault:
    def test_auto_commit_excludes_root_config_yaml(self, git_repo: Path) -> None:
        (git_repo / "config.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_API_KEY: sk-secret\n", encoding="utf-8"
        )
        (git_repo / "Patterns").mkdir()
        (git_repo / "Patterns" / "note.md").write_text("# Note\n", encoding="utf-8")
        # A nested config.yaml is NOT the vault-root secrets file and must
        # still be swept up by the default add-all path.
        (git_repo / "Projects").mkdir()
        (git_repo / "Projects" / "config.yaml").write_text("x: 1\n", encoding="utf-8")

        assert vault_fs.git_commit_vault("test commit", vault=git_repo) is True

        tracked = _git_ls_files(git_repo)
        assert "Patterns/note.md" in tracked
        assert "Projects/config.yaml" in tracked
        assert "config.yaml" not in tracked

    def test_auto_commit_succeeds_when_config_yaml_is_gitignored(
        self, git_repo: Path
    ) -> None:
        # config.yaml IS gitignored (the installer default). The redundant
        # :(exclude)config.yaml pathspec must NOT be emitted — it makes
        # `git add` exit 1 ("paths are ignored") and silently breaks every
        # auto-commit (regression: the 2026-07-29 commit stall).
        (git_repo / ".gitignore").write_text("config.yaml\n", encoding="utf-8")
        (git_repo / "config.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_API_KEY: sk-secret\n", encoding="utf-8"
        )
        (git_repo / "Patterns").mkdir()
        (git_repo / "Patterns" / "note.md").write_text("# Note\n", encoding="utf-8")

        assert vault_fs.git_commit_vault("test commit", vault=git_repo) is True

        tracked = _git_ls_files(git_repo)
        assert "Patterns/note.md" in tracked
        assert ".gitignore" in tracked
        assert "config.yaml" not in tracked  # gitignored -> not staged

    def test_explicit_paths_are_honored_unchanged(self, git_repo: Path) -> None:
        config = git_repo / "config.yaml"
        config.write_text("git:\n  auto_commit: true\n", encoding="utf-8")
        assert (
            vault_fs.git_commit_vault("explicit", vault=git_repo, paths=[config])
            is True
        )
        assert "config.yaml" in _git_ls_files(git_repo)

    def test_explicit_paths_do_not_commit_pre_staged_changes(
        self, git_repo: Path
    ) -> None:
        baseline = git_repo / "README.md"
        baseline.write_text("# Vault\n", encoding="utf-8")
        _git(["add", "README.md"], git_repo)
        _git(["commit", "-m", "baseline"], git_repo)

        unrelated = git_repo / "unrelated.md"
        unrelated.write_text("pending work\n", encoding="utf-8")
        _git(["add", "unrelated.md"], git_repo)

        generated = git_repo / "MANIFEST.md"
        generated.write_text("# Manifest\n", encoding="utf-8")
        assert (
            vault_fs.git_commit_vault(
                "rebuild index", vault=git_repo, paths=[generated]
            )
            is True
        )

        committed = subprocess.run(
            ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert committed == ["MANIFEST.md"]

        still_staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(git_repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        assert still_staged == ["unrelated.md"]


# ---------------------------------------------------------------------------
# vault_hooks — write_hook_event atomic rotation
# ---------------------------------------------------------------------------


class TestWriteHookEventRotation:
    def _configure(self, tmp_vault: Path, max_lines: int) -> None:
        (tmp_vault / "config.yaml").write_text(
            f"event_log:\n  enabled: true\n  max_lines: {max_lines}\n",
            encoding="utf-8",
        )
        vault_common.load_config.cache_clear()

    def test_rotation_keeps_second_half_plus_new_line(self, tmp_vault: Path) -> None:
        self._configure(tmp_vault, max_lines=4)
        for i in range(6):
            vault_hooks.write_hook_event("Test", "proj", 1.0, seq=i)
        log = tmp_vault / "hook_events.log"
        lines = [json.loads(line) for line in log.read_text().splitlines() if line]
        # Rotation at the 5th write keeps seq 2-3 + seq 4; the 6th appends.
        assert [entry["seq"] for entry in lines] == [2, 3, 4, 5]
        assert not (tmp_vault / "hook_events.log.tmp").exists()

    def test_rotation_replace_failure_preserves_log(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._configure(tmp_vault, max_lines=4)
        for i in range(4):
            vault_hooks.write_hook_event("Test", "proj", 1.0, seq=i)
        log = tmp_vault / "hook_events.log"
        original = log.read_text()
        monkeypatch.setattr(Path, "replace", _boom_replace)
        vault_hooks.write_hook_event("Test", "proj", 1.0, seq=99)  # must not raise
        assert log.read_text() == original


# ---------------------------------------------------------------------------
# build_graph — atomic graph.json write (fake numpy injected)
# ---------------------------------------------------------------------------


@pytest.fixture()
def build_graph_mod(monkeypatch: pytest.MonkeyPatch):
    if "numpy" not in sys.modules:
        # build_graph imports numpy at module level and annotates its helpers
        # with np.ndarray. Under Python <3.14 those annotations evaluate
        # eagerly at import time, so the stub must resolve any attribute —
        # the graph-JSON write path under test never touches numpy itself.
        fake_np = types.ModuleType("numpy")
        fake_np.__getattr__ = lambda name: object  # type: ignore[assignment]
        monkeypatch.setitem(sys.modules, "numpy", fake_np)
    sys.modules.pop("build_graph", None)
    mod = importlib.import_module("build_graph")
    yield mod
    sys.modules.pop("build_graph", None)


class TestWriteGraphJson:
    def test_writes_valid_json_without_tmp_leftover(
        self, build_graph_mod, tmp_path: Path
    ) -> None:
        out = tmp_path / "graph.json"
        graph = {"meta": {"note_count": 0}, "nodes": [], "edges": []}
        build_graph_mod.write_graph_json(graph, out)
        assert json.loads(out.read_text(encoding="utf-8")) == graph
        assert not (tmp_path / "graph.json.tmp").exists()

    def test_failed_write_preserves_existing_file(
        self, build_graph_mod, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out = tmp_path / "graph.json"
        out.write_text('{"nodes":[]}', encoding="utf-8")

        def boom(*args: object, **kwargs: object) -> None:
            raise OSError("simulated dump failure")

        monkeypatch.setattr(build_graph_mod.json, "dump", boom)
        with pytest.raises(OSError):
            build_graph_mod.write_graph_json({"nodes": [1]}, out)
        assert out.read_text(encoding="utf-8") == '{"nodes":[]}'


# ---------------------------------------------------------------------------
# QA-017 — update_index generated index files are atomic
# ---------------------------------------------------------------------------


class TestUpdateIndexAtomicWrites:
    """QA-017: update_index routes CLAUDE.md, TAGS.md, and MANIFEST.md writes
    through ``vault_fs.atomic_write_text``. A half-written CLAUDE.md would be
    read by ``session_start_hook`` at the next session start and inject
    truncated context into a live agent session — the atomic tmp+rename
    pattern means readers either see the previous version or the new one,
    never a partial write.
    """

    def test_claude_md_failure_leaves_original_intact(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Path.replace failure during CLAUDE.md rewrite preserves the
        original file byte-for-byte and leaves no .tmp residue."""
        import update_index

        # Seed an existing CLAUDE.md so atomic_write_text has a mode to copy.
        existing = "# Old index\n\nstale content\n"
        (tmp_vault / "CLAUDE.md").write_text(existing, encoding="utf-8")

        # Now break Path.replace and confirm the rewrite is atomic.
        monkeypatch.setattr(Path, "replace", _boom_replace)
        with pytest.raises(OSError):
            update_index.atomic_write_text(
                tmp_vault / "CLAUDE.md", "# New index\nnew content\n"
            )

        # Original content survived.
        assert (tmp_vault / "CLAUDE.md").read_text(encoding="utf-8") == existing
        # No .tmp residue.
        assert not (tmp_vault / "CLAUDE.md.tmp").exists()

    def test_manifest_md_failure_leaves_original_intact(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same atomic-write contract for per-folder MANIFEST.md files."""
        import update_index

        patterns_dir = tmp_vault / "Patterns"
        patterns_dir.mkdir(exist_ok=True)
        manifest = patterns_dir / "MANIFEST.md"
        existing = "# Old manifest\n| stale |\n"
        manifest.write_text(existing, encoding="utf-8")

        monkeypatch.setattr(Path, "replace", _boom_replace)
        with pytest.raises(OSError):
            update_index.atomic_write_text(manifest, "# New manifest\n")
        assert manifest.read_text(encoding="utf-8") == existing
        assert not (patterns_dir / "MANIFEST.md.tmp").exists()

    def test_tags_md_failure_leaves_original_intact(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same atomic-write contract for the root TAGS.md tag cloud."""
        import update_index

        existing = "# Old tags\n`python`\n"
        (tmp_vault / "TAGS.md").write_text(existing, encoding="utf-8")

        monkeypatch.setattr(Path, "replace", _boom_replace)
        with pytest.raises(OSError):
            update_index.atomic_write_text(
                tmp_vault / "TAGS.md", "# New tags\n`rust`\n"
            )
        assert (tmp_vault / "TAGS.md").read_text(encoding="utf-8") == existing


# ---------------------------------------------------------------------------
# QA-017 — vault_doctor graph.json rewrite is atomic
# ---------------------------------------------------------------------------


class TestVaultDoctorGraphJsonAtomic:
    """QA-017: vault_doctor._normalize_canonical_tags_in_graph writes the
    47.5 MB graph.json via vault_fs.atomic_write_text so an interrupt cannot
    leave a truncated file that breaks the visualizer's SSE rebuild."""

    def test_graph_json_failure_preserves_original(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import vault_doctor

        graph_path = tmp_vault / "graph.json"
        original = '{"nodes":[],"edges":[]}\n'
        graph_path.write_text(original, encoding="utf-8")

        monkeypatch.setattr(Path, "replace", _boom_replace)
        with pytest.raises(OSError):
            vault_doctor.vault_fs.atomic_write_text(graph_path, '{"nodes":[1]}\n')
        assert graph_path.read_text(encoding="utf-8") == original
        # atomic_write_text uses <name>.tmp (sibling), not <name>.json.tmp —
        # the legacy _write_json_atomic helper in vault_doctor uses the latter
        # which would leave stale .json.tmp residue in the vault root. Pin
        # the sibling naming so the right helper is in use.
        assert not (tmp_vault / "graph.json.tmp").exists()
        assert not (tmp_vault / "graph.tmp").exists()


# ---------------------------------------------------------------------------
# build_embeddings — full rebuild is transactional (fake fastembed injected)
# ---------------------------------------------------------------------------


@pytest.fixture()
def build_embeddings_mod(monkeypatch: pytest.MonkeyPatch):
    fake_vec = types.ModuleType("sqlite_vec")
    fake_vec.load = lambda conn: None  # type: ignore[attr-defined]

    class FakeTextEmbedding:
        fail = False

        def __init__(self, model_name: str) -> None:
            if FakeTextEmbedding.fail:
                raise RuntimeError("simulated model download failure")

        def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

    fake_fe = types.ModuleType("fastembed")
    fake_fe.TextEmbedding = FakeTextEmbedding  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sqlite_vec", fake_vec)
    monkeypatch.setitem(sys.modules, "fastembed", fake_fe)
    sys.modules.pop("build_embeddings", None)
    mod = importlib.import_module("build_embeddings")
    yield mod
    sys.modules.pop("build_embeddings", None)


def _seed_embeddings_db(mod, db_path: Path) -> None:
    conn = mod.open_db(db_path)
    with conn:
        conn.execute(
            "INSERT INTO note_embeddings (stem, path, embedding) VALUES (?, ?, ?)",
            ("old-note", "/old.md", b"\x00\x00\x00\x00"),
        )
    conn.close()


def _write_note(tmp_vault: Path) -> None:
    (tmp_vault / "Patterns").mkdir(parents=True, exist_ok=True)
    (tmp_vault / "Patterns" / "note-a.md").write_text(
        "---\ndate: 2026-01-01\ntype: pattern\ntags: [x]\n"
        'related: ["[[other]]"]\n---\n\n# Note A\n\nbody\n',
        encoding="utf-8",
    )


class TestFullRebuildTransactional:
    def test_model_failure_preserves_existing_index(
        self, build_embeddings_mod, tmp_vault: Path
    ) -> None:
        _write_note(tmp_vault)
        db_path = tmp_vault / "embeddings.db"
        _seed_embeddings_db(build_embeddings_mod, db_path)

        build_embeddings_mod.TextEmbedding.fail = True
        with pytest.raises(RuntimeError):
            build_embeddings_mod.full_rebuild(tmp_vault, "fake-model", dry_run=False)

        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT stem FROM note_embeddings").fetchall()
        conn.close()
        assert rows == [("old-note",)]

    def test_successful_rebuild_replaces_rows(
        self, build_embeddings_mod, tmp_vault: Path
    ) -> None:
        _write_note(tmp_vault)
        db_path = tmp_vault / "embeddings.db"
        _seed_embeddings_db(build_embeddings_mod, db_path)

        build_embeddings_mod.full_rebuild(tmp_vault, "fake-model", dry_run=False)

        conn = sqlite3.connect(db_path)
        stems = {row[0] for row in conn.execute("SELECT stem FROM note_embeddings")}
        conn.close()
        assert "note-a" in stems
        assert "old-note" not in stems

    def test_empty_vault_rebuild_still_clears_stale_rows(
        self, build_embeddings_mod, tmp_vault: Path
    ) -> None:
        db_path = tmp_vault / "embeddings.db"
        _seed_embeddings_db(build_embeddings_mod, db_path)

        build_embeddings_mod.full_rebuild(tmp_vault, "fake-model", dry_run=False)

        conn = sqlite3.connect(db_path)
        count = conn.execute("SELECT COUNT(*) FROM note_embeddings").fetchone()[0]
        conn.close()
        assert count == 0


def test_read_last_n_lines_byte_bound_drops_oldest(tmp_path: Path) -> None:
    """``max_bytes`` drops oldest lines until the retained tail fits."""
    line = "x" * 49 + "\n"  # 50 bytes
    f = tmp_path / "tail.log"
    f.write_text(line * 5, encoding="utf-8")  # 250 bytes, 5 lines

    assert len(vault_fs.read_last_n_lines(f, 400)) == 5  # no bound -> all
    bounded = vault_fs.read_last_n_lines(f, 400, max_bytes=110)  # keep newest <=110B
    assert len(bounded) == 2
    assert sum(len(ln.encode()) for ln in bounded) <= 110


def test_read_last_n_lines_byte_bound_keeps_most_recent(tmp_path: Path) -> None:
    """``max_bytes`` below a single line still keeps the most recent line."""
    f = tmp_path / "big.log"
    f.write_text("y" * 5000 + "\n", encoding="utf-8")
    got = vault_fs.read_last_n_lines(f, 400, max_bytes=10)
    assert len(got) == 1  # never returns empty when the file has content


def test_read_last_n_lines_byte_bound_none_is_backward_compatible(
    tmp_path: Path,
) -> None:
    """``max_bytes=None`` matches the legacy two-arg call."""
    f = tmp_path / "lines.log"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    assert vault_fs.read_last_n_lines(f, 400, max_bytes=None) == ["a\n", "b\n", "c\n"]
