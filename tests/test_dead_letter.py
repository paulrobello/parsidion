"""Tests for the dead-letter visibility enhancement.

Covers:
- summarize_sessions._append_dead_letter(): written on purge at _MAX_ATTEMPTS,
  best-effort (never raises) when the write itself fails.
- vault_metrics.collect_dead_letters(): parses dead_letters.jsonl.
- session_start_hook._build_dead_letter_notice(): warning line construction,
  and its inclusion in build_session_context()'s injected context.
"""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_metrics  # noqa: E402

session_start_hook = importlib.import_module("session_start_hook")


class _FakeSemaphore:
    def __init__(self, _: int = 1) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeTaskGroup:
    """Sequential stand-in for anyio.create_task_group()."""

    def __init__(self) -> None:
        self._tasks: list[tuple[object, tuple[object, ...]]] = []

    async def __aenter__(self) -> _FakeTaskGroup:
        return self

    def start_soon(self, fn: object, *args: object) -> None:
        self._tasks.append((fn, args))

    async def __aexit__(self, *exc: object) -> None:
        for fn, args in self._tasks:
            await fn(*args)  # type: ignore[operator]


def _fresh_summarize_sessions(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    """Import summarize_sessions.py with a stub anyio (real package not installed)."""
    monkeypatch.setitem(
        sys.modules,
        "anyio",
        types.SimpleNamespace(
            Semaphore=_FakeSemaphore,
            create_task_group=_FakeTaskGroup,
            to_thread=types.SimpleNamespace(run_sync=lambda func, *args: func(*args)),
        ),
    )
    sys.modules.pop("summarize_sessions", None)
    return importlib.import_module("summarize_sessions")


def _write_pending(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in entries),
        encoding="utf-8",
    )


def _read_pending_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# summarize_sessions: dead-letter write on purge
# ---------------------------------------------------------------------------


def test_purge_at_max_attempts_writes_dead_letter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    doomed: dict[str, object] = {
        "session_id": "dead1",
        "project": "myproj",
        "transcript_path": "/tmp/x.jsonl",
        "attempts": 2,
    }
    _write_pending(pending, [doomed])

    mod.remove_processed(pending, [], failed={"dead1": "merge target unresolvable"})

    dead_letter_path = tmp_path / "dead_letters.jsonl"
    assert dead_letter_path.exists()
    lines = [
        line
        for line in dead_letter_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == "dead1"
    assert record["project"] == "myproj"
    assert record["transcript_path"] == "/tmp/x.jsonl"
    assert record["attempts"] == 3
    assert record["last_failure"] == "merge target unresolvable"
    assert "dead_lettered_at" in record and record["dead_lettered_at"]

    # Permissions match the 0o600 convention used by vault_fs.append_to_pending.
    mode = dead_letter_path.stat().st_mode & 0o777
    assert mode == 0o600

    # Entry is gone from the pending queue.
    assert _read_pending_lines(pending) == []


def test_dead_letter_appends_across_multiple_purges(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"

    _write_pending(pending, [{"session_id": "d1", "project": "p1", "attempts": 2}])
    mod.remove_processed(pending, [], failed={"d1": "boom1"})

    _write_pending(pending, [{"session_id": "d2", "project": "p2", "attempts": 2}])
    mod.remove_processed(pending, [], failed={"d2": "boom2"})

    dead_letter_path = tmp_path / "dead_letters.jsonl"
    lines = [
        json.loads(line)
        for line in dead_letter_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [r["session_id"] for r in lines] == ["d1", "d2"]


# ---------------------------------------------------------------------------
# summarize_sessions: dead-letter write is best-effort
# ---------------------------------------------------------------------------


def test_dead_letter_write_failure_is_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    _write_pending(
        pending, [{"session_id": "dead2", "project": "myproj", "attempts": 2}]
    )

    # SEC-109: remove_processed now creates its queue tmp via os.open (0o600
    # perms) too, so a global os.open patch would break the queue rewrite as
    # well. Capture the real os.open before patching and only fail opens that
    # target dead_letters.jsonl; the queue rewrite (.jsonl.tmp) keeps working,
    # preserving the test's original intent ("dead-letter write failure does
    # not resurrect the queue entry").
    _real_os_open = mod.os.open

    def _boom(*args: object, **kwargs: object) -> int:
        path_arg = str(args[0]) if args else ""
        if "dead_letters.jsonl" in path_arg:
            raise OSError("disk full")
        return _real_os_open(*args, **kwargs)

    monkeypatch.setattr(mod.os, "open", _boom)

    # Must not raise even though the dead-letter write itself fails.
    mod.remove_processed(pending, [], failed={"dead2": "boom"})

    # The entry is still purged from the queue -- the dead-letter write
    # failure only loses visibility, it must not resurrect the entry.
    assert _read_pending_lines(pending) == []
    assert not (tmp_path / "dead_letters.jsonl").exists()

    err = capsys.readouterr().err
    assert "could not write dead-letter record" in err


# ---------------------------------------------------------------------------
# ARC-013 / SEC-129: prune must hold the lock around the read
# ---------------------------------------------------------------------------


def test_arc013_prune_does_not_lose_concurrent_append(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A concurrent _append_dead_letter during _prune_dead_letters is preserved.

    Reproduces the original race: the unlocked read at the old :1695-1698 ran
    before LOCK_EX was taken at :1720. An _append_dead_letter landing in that
    window was destroyed by the subsequent truncate. Now the read happens
    inside the lock, so concurrent appends serialize and are preserved.

    We exercise this with a thread that hammers _append_dead_letter while a
    prune loop runs; every appended entry must survive.
    """
    import threading

    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path
    dl = vault / "dead_letters.jsonl"

    # Seed an old entry that will be pruned.
    from datetime import datetime, timedelta

    old_ts = (datetime.now() - timedelta(days=30)).isoformat()
    _write_pending(
        dl,
        [{"session_id": "old", "dead_lettered_at": old_ts}],
    )

    stop = threading.Event()
    appended: list[str] = []
    errors: list[str] = []

    def appender() -> None:
        i = 0
        while not stop.is_set():
            entry = {
                "session_id": f"concurrent-{i}",
                "dead_lettered_at": datetime.now().isoformat(),
            }
            try:
                mod._append_dead_letter(dl, entry, 0, "x")
                appended.append(entry["session_id"])
                i += 1
            except Exception as e:  # noqa: BLE001
                errors.append(str(e))

    t = threading.Thread(target=appender, daemon=True)
    t.start()
    # Run prune several times concurrently with the appender.
    for _ in range(5):
        mod._prune_dead_letters(vault, retention_days=7)
    stop.set()
    t.join(timeout=5)

    # All appends that succeeded must appear in the final file. Under the
    # original unlocked-read bug, some were lost to the in-place truncate.
    remaining_sids = {
        json.loads(line).get("session_id")
        for line in dl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    lost = [s for s in appended if s not in remaining_sids]
    assert not lost, (
        f"{len(lost)} appended entries lost during concurrent prune: {lost[:3]}..."
    )
    # The old entry should always have been pruned (it's well past retention).
    assert "old" not in remaining_sids


def test_arc013_prune_preserves_0600_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SEC-109 / ARC-013: pruned dead_letters.jsonl stays at mode 0o600."""
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path
    dl = vault / "dead_letters.jsonl"

    from datetime import datetime, timedelta

    old_ts = (datetime.now() - timedelta(days=30)).isoformat()
    new_ts = datetime.now().isoformat()
    # Create with 0600 to mirror the production creation path.
    import os as _os

    fd = _os.open(str(dl), _os.O_CREAT | _os.O_WRONLY, 0o600)
    with open(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps({"session_id": "old", "dead_lettered_at": old_ts}) + "\n")
        f.write(json.dumps({"session_id": "new", "dead_lettered_at": new_ts}) + "\n")

    pruned = mod._prune_dead_letters(vault, retention_days=7)
    assert pruned == 1
    mode = dl.stat().st_mode & 0o777
    assert mode == 0o600, f"dead_letters.jsonl mode regressed to {oct(mode)}"


# ---------------------------------------------------------------------------
# vault_metrics.collect_dead_letters
# ---------------------------------------------------------------------------


def test_collect_dead_letters_absent_file(tmp_path: Path) -> None:
    data = vault_metrics.collect_dead_letters(tmp_path)
    assert data == {"exists": False, "total": 0, "recent": []}


def test_collect_dead_letters_parses_file(tmp_path: Path) -> None:
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    records = [
        {
            "session_id": "a",
            "project": "p1",
            "last_failure": "err1",
            "dead_lettered_at": "2026-01-01T00:00:00",
        },
        {
            "session_id": "b",
            "project": "p2",
            "last_failure": "err2",
            "dead_lettered_at": "2026-01-02T00:00:00",
        },
        {
            "session_id": "c",
            "project": "p3",
            "last_failure": "err3",
            "dead_lettered_at": "2026-01-03T00:00:00",
        },
        {
            "session_id": "d",
            "project": "p4",
            "last_failure": "err4",
            "dead_lettered_at": "2026-01-04T00:00:00",
        },
    ]
    dead_letter_path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )

    data = vault_metrics.collect_dead_letters(tmp_path)

    assert data["exists"] is True
    assert data["total"] == 4
    assert len(data["recent"]) == 3
    # Capped at 3, most-recently-written entry first.
    assert data["recent"][0] == {
        "project": "p4",
        "last_failure": "err4",
        "dead_lettered_at": "2026-01-04T00:00:00",
    }
    assert data["recent"][-1]["project"] == "p2"


def test_collect_dead_letters_skips_malformed_lines(tmp_path: Path) -> None:
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    dead_letter_path.write_text(
        '{"session_id": "ok", "project": "p1"}\nnot-json\n', encoding="utf-8"
    )

    data = vault_metrics.collect_dead_letters(tmp_path)

    assert data["total"] == 1
    assert data["recent"][0]["project"] == "p1"


# ---------------------------------------------------------------------------
# session_start_hook._build_dead_letter_notice
# ---------------------------------------------------------------------------


def test_build_dead_letter_notice_empty_when_absent(tmp_path: Path) -> None:
    assert session_start_hook._build_dead_letter_notice(tmp_path) == ""


def test_build_dead_letter_notice_empty_when_file_empty(tmp_path: Path) -> None:
    (tmp_path / "dead_letters.jsonl").write_text("", encoding="utf-8")
    assert session_start_hook._build_dead_letter_notice(tmp_path) == ""


def test_build_dead_letter_notice_appears_when_nonempty(tmp_path: Path) -> None:
    dead_letter_path = tmp_path / "dead_letters.jsonl"
    dead_letter_path.write_text(
        json.dumps({"session_id": "a", "project": "p1"})
        + "\n"
        + json.dumps({"session_id": "b", "project": "p2"})
        + "\n",
        encoding="utf-8",
    )

    notice = session_start_hook._build_dead_letter_notice(tmp_path)

    assert notice.startswith("⚠ 2 session summary(ies) were dead-lettered")
    assert "vault-stats --pending" in notice
    assert str(dead_letter_path) in notice


def test_build_dead_letter_notice_swallows_read_errors(tmp_path: Path) -> None:
    # A directory named dead_letters.jsonl makes open() raise OSError
    # (IsADirectoryError) without relying on permission bits, which can be
    # unreliable across platforms/CI.
    (tmp_path / "dead_letters.jsonl").mkdir()
    assert session_start_hook._build_dead_letter_notice(tmp_path) == ""


# ---------------------------------------------------------------------------
# session_start_hook.build_session_context: notice is wired into the output
# ---------------------------------------------------------------------------


def _use_vault(monkeypatch: pytest.MonkeyPatch, vault: Path) -> None:
    """Point vault_common at *vault* and clear the resolver/config caches."""
    monkeypatch.setattr(vault_common, "VAULT_ROOT", vault)
    session_start_hook.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]
    vault_common._clear_config_cache()


def test_dead_letter_notice_included_in_session_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    for d in vault_common.VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    (vault / "config.yaml").write_text(
        "session_start_hook:\n"
        "  use_embeddings: false\n"
        "  track_delta: false\n"
        "  graph_expand: false\n",
        encoding="utf-8",
    )
    _use_vault(monkeypatch, vault)
    monkeypatch.setattr(session_start_hook, "find_notes_by_project", lambda project: [])
    monkeypatch.setattr(session_start_hook, "find_recent_notes", lambda days=3: [])
    monkeypatch.setattr(session_start_hook, "_run_semantic_search", lambda *a, **k: [])

    (vault / "dead_letters.jsonl").write_text(
        json.dumps({"session_id": "a", "project": "p1"})
        + "\n"
        + json.dumps({"session_id": "b", "project": "p2"})
        + "\n",
        encoding="utf-8",
    )

    context, _count = session_start_hook.build_session_context(cwd=str(vault))

    assert "⚠ 2 session summary(ies) were dead-lettered" in context
    assert "vault-stats --pending" in context


def test_no_dead_letter_notice_when_file_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    for d in vault_common.VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    (vault / "config.yaml").write_text(
        "session_start_hook:\n"
        "  use_embeddings: false\n"
        "  track_delta: false\n"
        "  graph_expand: false\n",
        encoding="utf-8",
    )
    _use_vault(monkeypatch, vault)
    monkeypatch.setattr(session_start_hook, "find_notes_by_project", lambda project: [])
    monkeypatch.setattr(session_start_hook, "find_recent_notes", lambda days=3: [])
    monkeypatch.setattr(session_start_hook, "_run_semantic_search", lambda *a, **k: [])

    context, _count = session_start_hook.build_session_context(cwd=str(vault))

    assert "dead-lettered" not in context


# ---------------------------------------------------------------------------
# ARC-030: non-retryable failures dead-letter on attempt 1
# ---------------------------------------------------------------------------


def test_arc030_non_retryable_failure_dead_letters_on_attempt_1(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failure marked retryable=False must dead-letter on the FIRST attempt,
    not after _MAX_ATTEMPTS retries. Re-billing an AI call to re-derive the
    same validation failure wastes money and re-touches the same target note."""
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    # Fresh entry with no prior attempts counter.
    _write_pending(pending, [{"session_id": "v1", "project": "p"}])

    # Structured record carrying retryable=False (the shape produced by
    # _mark_failure for FailureReason.MERGE_VALIDATION).
    failed = {
        "v1": {
            "kind": "merge_validation",
            "retryable": False,
            "detail": "Note has no YAML frontmatter block",
        }
    }

    mod.remove_processed(pending, [], failed=failed)

    dead_letter_path = tmp_path / "dead_letters.jsonl"
    assert dead_letter_path.exists(), (
        "non-retryable failure must dead-letter immediately"
    )
    record = json.loads(dead_letter_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["session_id"] == "v1"
    # attempts=1 (the dead-letter write captures the current attempt count)
    assert record["attempts"] == 1
    # The structured detail surfaces in last_failure for human inspection.
    assert "merge_validation" in record["last_failure"]
    assert "no YAML frontmatter" in record["last_failure"]
    # Entry was purged from the queue.
    assert _read_pending_lines(pending) == []
    err = capsys.readouterr().err
    assert "non-retryable" in err


def test_arc030_retryable_failure_still_uses_attempts_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A retryable=True failure must still wait for _MAX_ATTEMPTS before being
    dead-lettered (preserves the existing behavior for transient errors)."""
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    _write_pending(pending, [{"session_id": "r1", "project": "p"}])

    failed = {
        "r1": {
            "kind": "ai_backend_error",
            "retryable": True,
            "detail": "connection reset",
        }
    }
    mod.remove_processed(pending, [], failed=failed)

    # NOT dead-lettered yet — entry remains in queue with attempts=1.
    assert not (tmp_path / "dead_letters.jsonl").exists()
    remaining = _read_pending_lines(pending)
    assert len(remaining) == 1
    record = json.loads(remaining[0])
    assert record["attempts"] == 1


def test_arc030_legacy_string_failure_treated_as_retryable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Entries queued by pre-ARC-030 code carry a plain-string failure reason
    and no retryable field. These must continue to get the _MAX_ATTEMPTS retry
    budget rather than being dead-lettered on sight."""
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    _write_pending(pending, [{"session_id": "legacy1", "project": "p"}])

    failed = {"legacy1": "some legacy reason string"}
    mod.remove_processed(pending, [], failed=failed)

    assert not (tmp_path / "dead_letters.jsonl").exists()
    remaining = _read_pending_lines(pending)
    assert len(remaining) == 1


# ---------------------------------------------------------------------------
# QA-005: rebuild_index must swallow a graph-rebuild timeout, not propagate it
# ---------------------------------------------------------------------------


def test_rebuild_index_timeout_is_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """QA-005: a hung update_index/build_graph child must not propagate
    ``subprocess.TimeoutExpired`` into the caller (the summarizer and the
    hooks that drive it). ``rebuild_index`` logs a warning and returns,
    leaving the run to continue with a stale index rather than dying mid-run.
    """
    mod = _fresh_summarize_sessions(monkeypatch)

    def _timeout(*args: object, **kwargs: object) -> object:
        raise mod.subprocess.TimeoutExpired(cmd=["uv"], timeout=300)

    monkeypatch.setattr(mod.subprocess, "run", _timeout)

    # Must not raise.
    mod.rebuild_index(tmp_path, rebuild_graph=True)

    err = capsys.readouterr().err
    assert "timed out" in err.lower()
    assert "300" in err
