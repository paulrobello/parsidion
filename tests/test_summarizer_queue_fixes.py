"""Tests for summarizer queue robustness fixes.

Covers:
- remove_processed(): crash-atomic rewrite preserves unrelated entries
- remove_processed(): attempts increments on failure, dead-letter purge at cap
- run_all(): progress counter classification (written/skipped/errors)
- summarize_one(): merge decision with unresolvable/missing target fails with
  the real reason instead of falling through to the generic write path
- _requeue_dead_letters(): records whose transcript no longer exists are
  skipped (never re-queued), reported separately, and pruned
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


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
    mod = importlib.import_module("summarize_sessions")
    # Unit tests simulate completed/idle sessions; disable the active-session
    # guard by default. Tests that exercise the guard re-enable it explicitly.
    monkeypatch.setattr(mod, "_ACTIVE_SESSION_GRACE_SECS", 0)
    # QA-003: _early_gate now lives in summarizer.pipeline and reads
    # _ACTIVE_SESSION_GRACE_SECS from ITS globals; default-disable there too.
    import summarizer.pipeline as _pipeline

    monkeypatch.setattr(_pipeline, "_ACTIVE_SESSION_GRACE_SECS", 0)
    return mod


def _pipeline_module() -> types.ModuleType:
    """Return ``summarizer.pipeline`` for monkeypatching (QA-003).

    ``summarize_one`` and its stage helpers moved into ``summarizer.pipeline``;
    their bare-name dependencies resolve there, so patches must target that
    module. Imported lazily — safe after ``_fresh_summarize_sessions``.
    """
    import summarizer.pipeline

    return summarizer.pipeline


def _write_pending(path: Path, entries: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(e) + "\n" for e in entries),
        encoding="utf-8",
    )


def _read_pending_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


# ---------------------------------------------------------------------------
# remove_processed: atomic rewrite
# ---------------------------------------------------------------------------


def test_remove_processed_preserves_unrelated_entries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    e1: dict[str, object] = {"session_id": "s1", "project": "p1"}
    e2: dict[str, object] = {"session_id": "s2", "project": "p2"}
    e3: dict[str, object] = {"session_id": "s3", "project": "p3", "attempts": 2}
    _write_pending(pending, [e1, e2])
    with open(pending, "a", encoding="utf-8") as f:
        f.write("not-json\n")
        f.write(json.dumps(e3) + "\n")

    mod.remove_processed(pending, [e1])

    lines = _read_pending_lines(pending)
    assert len(lines) == 3
    assert json.loads(lines[0]) == e2
    assert lines[1] == "not-json"  # malformed lines are kept verbatim
    assert json.loads(lines[2]) == e3  # attempts untouched when not failed
    # Atomic swap leaves no sibling temp file behind
    assert not pending.with_suffix(".jsonl.tmp").exists()


# ---------------------------------------------------------------------------
# remove_processed: attempts increment + dead-letter purge
# ---------------------------------------------------------------------------


def test_remove_processed_increments_attempts_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    e1: dict[str, object] = {"session_id": "s1", "project": "p1"}
    e2: dict[str, object] = {"session_id": "s2", "project": "p2"}
    _write_pending(pending, [e1, e2])

    mod.remove_processed(pending, [], failed={"s1": "AI backend error: boom"})

    entries = [json.loads(line) for line in _read_pending_lines(pending)]
    assert entries[0]["session_id"] == "s1"
    assert entries[0]["attempts"] == 1  # absent field defaults to 0, then +1
    assert "attempts" not in entries[1]

    mod.remove_processed(pending, [], failed={"s1": "AI backend error: boom"})
    entries = [json.loads(line) for line in _read_pending_lines(pending)]
    assert entries[0]["attempts"] == 2


def test_remove_processed_purges_at_attempts_cap_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    doomed: dict[str, object] = {
        "session_id": "dead1",
        "project": "myproj",
        "attempts": 2,
    }
    survivor: dict[str, object] = {"session_id": "s2", "project": "p2"}
    _write_pending(pending, [doomed, survivor])

    mod.remove_processed(pending, [], failed={"dead1": "merge target unresolvable"})

    entries = [json.loads(line) for line in _read_pending_lines(pending)]
    assert [e["session_id"] for e in entries] == ["s2"]
    err = capsys.readouterr().err
    assert "dead1" in err
    assert "myproj" in err
    assert "merge target unresolvable" in err
    assert "3 failed attempts" in err


# ---------------------------------------------------------------------------
# remove_processed: write-gate skip retry budget (stochastic-skip recovery)
# ---------------------------------------------------------------------------


def test_remove_processed_requeues_first_write_gate_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A first write-gate skip is re-queued (skips bumped), not dead-lettered.

    The gate is stochastic on borderline sessions, so one skip must not
    permanently shelve a recoverable session.
    """
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    skipped_entry: dict[str, object] = {"session_id": "skip1", "project": "p1"}
    other: dict[str, object] = {"session_id": "s2", "project": "p2"}
    _write_pending(pending, [skipped_entry, other])

    mod.remove_processed(pending, [], skip_retry={"skip1"})

    entries = [json.loads(line) for line in _read_pending_lines(pending)]
    assert [e["session_id"] for e in entries] == ["skip1", "s2"]
    skip1 = next(e for e in entries if e["session_id"] == "skip1")
    assert skip1["skips"] == 1  # absent -> 0 -> +1
    # Not dead-lettered on the first skip.
    assert not (tmp_path / "dead_letters.jsonl").exists()


def test_remove_processed_dead_letters_skip_at_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A skip reaching _MAX_SKIPS (2) is sticky dead-lettered + purged."""
    mod = _fresh_summarize_sessions(monkeypatch)
    pending = tmp_path / "pending_summaries.jsonl"
    # One skip already recorded (one re-evaluation happened); this run is the
    # _MAX_SKIPS-th (2nd) consecutive skip -> sticky dead-letter.
    terminal: dict[str, object] = {
        "session_id": "skip1",
        "project": "myproj",
        "skips": 1,
    }
    survivor: dict[str, object] = {"session_id": "s2", "project": "p2"}
    _write_pending(pending, [terminal, survivor])

    mod.remove_processed(pending, [], skip_retry={"skip1"})

    entries = [json.loads(line) for line in _read_pending_lines(pending)]
    assert [e["session_id"] for e in entries] == ["s2"]  # skip1 purged
    dead = (tmp_path / "dead_letters.jsonl").read_text(encoding="utf-8").splitlines()
    rec = json.loads(dead[0])
    assert rec["session_id"] == "skip1"
    assert rec["last_failure"] == "write-gate skip (transient)"
    err = capsys.readouterr().err
    assert "skip1" in err
    assert "2 write-gate skips" in err


# ---------------------------------------------------------------------------
# run_all: progress counter classification
# ---------------------------------------------------------------------------


def test_run_all_classifies_progress_counters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    monkeypatch.setattr(mod.vault_common, "all_vault_notes", lambda vault: [])
    monkeypatch.setattr(mod, "read_existing_tags", lambda vault: [])
    monkeypatch.setattr(
        mod, "read_project_names", lambda vault_notes=None, vault=None: set()
    )

    outcomes: dict[str, object] = {
        "a": tmp_path / "note.md",  # written
        "b": mod._STALE,  # skipped (stale)
        "c": mod._SKIPPED,  # skipped (write-gate)
        "d": None,  # error
    }

    async def fake_summarize_one(
        entry: dict[str, object], *args: object, **kwargs: object
    ) -> tuple[dict[str, object], object]:
        return entry, outcomes[str(entry["session_id"])]

    monkeypatch.setattr(mod, "summarize_one", fake_summarize_one)

    progress_calls: list[dict[str, int]] = []

    def fake_write_progress(
        total: int,
        processed: int,
        written: int,
        skipped: int,
        errors: int,
        current: str = "",
    ) -> None:
        progress_calls.append(
            {
                "total": total,
                "processed": processed,
                "written": written,
                "skipped": skipped,
                "errors": errors,
            }
        )

    monkeypatch.setattr(mod, "_write_progress", fake_write_progress)

    entries: list[dict[str, object]] = [
        {"session_id": sid, "project": "p"} for sid in ("a", "b", "c", "d")
    ]
    results = asyncio.run(mod.run_all(entries, None, False, False, tmp_path))

    assert len(results) == 4
    final = progress_calls[-1]
    assert final == {
        "total": 4,
        "processed": 4,
        "written": 1,
        "skipped": 2,
        "errors": 1,
    }


# ---------------------------------------------------------------------------
# summarize_one: merge decision failure handling
# ---------------------------------------------------------------------------


def _prepare_merge_test(
    mod: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    decision: dict[str, object],
) -> dict[str, object]:
    """Wire summarize_one fakes so the backend returns *decision* JSON."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned dialogue"

    async def fake_prompt(*args: object, **kwargs: object) -> object:
        return json.dumps(decision), None

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt_with_cause", fake_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )
    monkeypatch.setattr(_pipeline_module(), "build_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(
        mod.vault_common, "get_config", lambda section, key, default=None: default
    )
    return {
        "session_id": "sess-merge",
        "transcript_path": str(transcript),
        "project": "proj",
        "categories": [],
    }


def test_merge_with_unresolvable_target_fails_with_real_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    entry = _prepare_merge_test(
        mod,
        monkeypatch,
        tmp_path,
        {"decision": "merge", "target": "[[missing-note]]", "new_content": "body"},
    )
    monkeypatch.setattr(
        _pipeline_module(), "_resolve_note_stem", lambda stem, vault: None
    )
    # The generic write path must never be reached with raw decision JSON
    monkeypatch.setattr(
        _pipeline_module(),
        "write_note",
        lambda *a, **k: pytest.fail("fell through to generic write path"),
    )

    result_entry, written = asyncio.run(
        mod.summarize_one(entry, None, False, _FakeSemaphore(), [], False, tmp_path)
    )

    assert result_entry is entry
    assert written is None  # failure form: preserved for retry, attempts-capped
    err = capsys.readouterr().err
    assert "merge target [[missing-note]] could not be resolved" in err
    # ARC-030: _FAILURE_REASON_KEY is now a structured record carrying the
    # classified kind + retryable flag (non-retryable for merge-unresolvable)
    # rather than a free-text string.
    record = entry[mod._FAILURE_REASON_KEY]
    assert isinstance(record, dict)
    assert record["kind"] == "merge_unresolvable"
    assert record["retryable"] is False
    assert "merge target [[missing-note]] could not be resolved" in record["detail"]


def test_merge_with_missing_fields_fails_with_real_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    entry = _prepare_merge_test(mod, monkeypatch, tmp_path, {"decision": "merge"})
    monkeypatch.setattr(
        _pipeline_module(),
        "write_note",
        lambda *a, **k: pytest.fail("fell through to generic write path"),
    )

    result_entry, written = asyncio.run(
        mod.summarize_one(entry, None, False, _FakeSemaphore(), [], False, tmp_path)
    )

    assert result_entry is entry
    assert written is None
    err = capsys.readouterr().err
    assert "merge decision missing target or new_content" in err
    # ARC-030: structured record, classified merge_malformed (non-retryable).
    record = entry[mod._FAILURE_REASON_KEY]
    assert isinstance(record, dict)
    assert record["kind"] == "merge_malformed"
    assert record["retryable"] is False
    assert record["detail"] == "merge decision missing target or new_content"


# ---------------------------------------------------------------------------
# _backfill_tags_if_empty: salvage notes the model emitted with empty/absent tags
# ---------------------------------------------------------------------------

_FM_HEAD = '---\ndate: 2026-07-12\ntype: {ntype}\n{tagsline}related: ["[[x]]"]\n---\n# T\nbody\n'


def _note(ntype: str, tagsline: str) -> str:
    return _FM_HEAD.format(ntype=ntype, tagsline=tagsline)


def test_backfill_empty_inline_tags_derives_from_type_project_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    out = mod._backfill_tags_if_empty(
        _note("pattern", "tags: []\n"), "voxel-world", ["error_fix"]
    )
    assert "tags: [pattern, voxel-world, error-fix]" in out
    assert mod._validate_frontmatter(out) is None


def test_backfill_missing_tags_line_inserts_after_delimiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    out = mod._backfill_tags_if_empty(_note("tool", ""), ".claude", ["pattern"])
    # leading dot stripped (.claude -> claude), inserted right after opening ---
    assert out.startswith("---\ntags: [tool, claude, pattern]\n")
    assert mod._validate_frontmatter(out) is None


def test_backfill_does_not_clobber_valid_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    note = _note("pattern", "tags: [react, hook]\n")
    assert mod._backfill_tags_if_empty(note, "voxel-world", ["error_fix"]) == note


def test_backfill_cleans_underscores_and_dots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    out = mod._backfill_tags_if_empty(
        _note("debugging", "tags: []\n"), "par_ai_core", ["config_setup"]
    )
    assert "tags: [debugging, par-ai-core, config-setup]" in out


def test_backfill_replaces_empty_yaml_list_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    # Non-empty YAML list is left untouched.
    populated = (
        "---\ndate: 2026-07-12\ntype: pattern\ntags:\n  - one\n"
        'related: ["[[x]]"]\n---\n# T\n'
    )
    assert "  - one" in mod._backfill_tags_if_empty(populated, "proj", [])
    # Empty YAML list (tags:\n with no items) is backfilled.
    empty_block = (
        '---\ndate: 2026-07-12\ntype: pattern\ntags:\nrelated: ["[[x]]"]\n---\n# T\n'
    )
    out = mod._backfill_tags_if_empty(empty_block, "proj", [])
    assert "tags: [pattern, proj]" in out
    assert mod._validate_frontmatter(out) is None


def test_backfill_type_alone_suffices_when_project_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    # Unknown project, no categories: the note type alone yields a valid tag,
    # which is the realistic minimal case that prevents the empty-tags failure.
    out = mod._backfill_tags_if_empty(_note("pattern", "tags: []\n"), "unknown", [])
    assert "tags: [pattern]" in out
    assert mod._validate_frontmatter(out) is None


def test_summarize_one_purges_dead_lettered_requeue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A session already in dead_letters.jsonl (prior failure or write-gate
    skip) that a stop hook re-queued must not be re-processed — it is purged
    via the _DEAD path instead of re-billing an AI call."""
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type":"user","content":"x"}\n', encoding="utf-8")
    # Pretend a prior run already dead-lettered this session.
    (vault / "dead_letters.jsonl").write_text(
        json.dumps({"session_id": "dead-1", "project": "p"}) + "\n",
        encoding="utf-8",
    )
    entry = {
        "transcript_path": str(transcript),
        "project": "p",
        "categories": [],
        "session_id": "dead-1",
    }

    _entry, written = asyncio.run(
        mod.summarize_one(entry, None, False, mod.anyio.Semaphore(1), [], False, vault)
    )

    assert written == mod._DEAD


# ---------------------------------------------------------------------------
# _requeue_dead_letters: unrecoverable records (transcript gone)
# ---------------------------------------------------------------------------


def _dl_record(
    sid: str, transcript: str, *, last_failure: str | None = None
) -> dict[str, object]:
    """Build a dead_letters.jsonl record for *sid* with *transcript*."""
    failure = (
        last_failure if last_failure is not None else f"no_result_timeout: {transcript}"
    )
    return {
        "session_id": sid,
        "transcript_path": transcript,
        "project": "p",
        "categories": ["x"],
        "timestamp": "2026-08-01T00:00:00",
        "source": "session",
        "attempts": 3,
        "last_failure": failure,
        "dead_lettered_at": "2026-08-01T00:00:00",
    }


def test_requeue_dead_letters_skips_and_prunes_missing_transcripts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead letter whose transcript no longer exists (typically removed
    with its git worktree) can never succeed: re-queuing it burns a pipeline
    slot only to fail and re-dead-letter. Such records must be skipped and
    pruned from dead_letters.jsonl, not re-queued."""
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    live_a = tmp_path / "live-a.jsonl"
    live_a.write_text("{}\n", encoding="utf-8")
    live_b = tmp_path / "live-b.jsonl"
    live_b.write_text("{}\n", encoding="utf-8")
    records = [
        _dl_record("live-a", str(live_a)),
        _dl_record("gone-a", str(tmp_path / "gone-a.jsonl")),  # never created
        _dl_record("gone-b", str(tmp_path / "gone-b.jsonl")),  # never created
        _dl_record("live-b", str(live_b)),
        _dl_record(
            "other",
            str(tmp_path / "other.jsonl"),
            last_failure="note_validation: write_note returned None",
        ),
    ]
    dl = vault / "dead_letters.jsonl"
    _write_pending(dl, records)
    pending = vault / "pending_summaries.jsonl"
    pending.write_text("", encoding="utf-8")

    requeue = mod._requeue_dead_letters

    # Dry-run counts without mutating: 2 recoverable, 2 unrecoverable.
    result = requeue(
        vault, reason="no_result", min_age_days=0, max_count=0, dry_run=True
    )
    assert (result.requeued, result.unrecoverable) == (2, 2)
    assert len(_read_pending_lines(dl)) == 5

    # Real run re-queues only the two live transcripts...
    result = requeue(vault, reason="no_result", min_age_days=0, max_count=0)
    assert (result.requeued, result.unrecoverable) == (2, 2)
    moved = [json.loads(line) for line in _read_pending_lines(pending)]
    assert {e["session_id"] for e in moved} == {"live-a", "live-b"}
    # ...and drops the unrecoverable matches from dead_letters.jsonl; only the
    # record that never matched the reason filter survives.
    remaining = [json.loads(line) for line in _read_pending_lines(dl)]
    assert [r["session_id"] for r in remaining] == ["other"]


def test_main_retry_dead_letters_dry_run_reports_unrecoverable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The --retry-dead-letters report states the unrecoverable count
    separately from the re-queue count, so the operator sees why the retry
    batch is smaller than the file."""
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    live = tmp_path / "live.jsonl"
    live.write_text("{}\n", encoding="utf-8")
    records = [
        _dl_record("live-1", str(live)),
        _dl_record("gone-1", str(tmp_path / "gone-1.jsonl")),
    ]
    # Fresh timestamps: main() prunes dead letters older than the retention
    # window (default 7 days) BEFORE the retry pass, and these must survive it.
    now = datetime.now().isoformat()
    for record in records:
        record["dead_lettered_at"] = now
    _write_pending(vault / "dead_letters.jsonl", records)
    (vault / "pending_summaries.jsonl").write_text("", encoding="utf-8")

    # A tmp vault is not an allowlisted vault; resolve it like main() would.
    monkeypatch.setattr(mod.vault_common, "resolve_vault", lambda **_: vault)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_sessions.py",
            "--vault",
            str(vault),
            "--retry-dead-letters",
            "--dry-run",
        ],
    )

    mod.main()

    out = capsys.readouterr().out
    assert "would re-queue 1 dead-lettered session(s)" in out
    assert "1 unrecoverable" in out


# ---------------------------------------------------------------------------
# ARC-106: max_parallel clamp + doctor sub-run through the shared pgkill owner
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (500, 16),  # above the ceiling
        (16, 16),  # exactly the ceiling
        (5, 5),  # in range, untouched
        (1, 1),
        (0, 1),  # "run one at a time", not "fall back to the default"
        (-3, 1),
        (True, 5),  # bool is not a concurrency; falls back to the default
        ("abc", 5),  # YAML can hand back a string
        (float("nan"), 5),
        (float("inf"), 5),  # non-finite is not a concurrency either
    ],
)
def test_clamp_max_parallel(
    monkeypatch: pytest.MonkeyPatch, configured: object, expected: int
) -> None:
    """Every unit of fan-out is a CLI agent process, so the value must be bounded."""
    mod = _fresh_summarize_sessions(monkeypatch)
    assert mod._clamp_max_parallel(configured) == expected


def test_clamp_max_parallel_warns_only_when_clamped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)

    mod._clamp_max_parallel(5)
    assert capsys.readouterr().err == ""

    mod._clamp_max_parallel(500)
    assert "clamped to 16" in capsys.readouterr().err


def test_resolve_options_clamps_configured_max_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clamp is wired into the option resolution, not just available."""
    mod = _fresh_summarize_sessions(monkeypatch)
    real_get_config = mod.vault_common.get_config

    def _fake_get_config(section: str, key: str, default: object = None, **kw: object):
        if section == "summarizer" and key == "max_parallel":
            return 500
        return real_get_config(section, key, default, **kw)

    monkeypatch.setattr(mod.vault_common, "get_config", _fake_get_config)
    args = mod._build_parser().parse_args([])
    assert mod._resolve_options(args).max_parallel == 16


def test_run_doctor_goes_through_run_with_pgkill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The doctor sub-run must not use a bare subprocess.run.

    A plain timeout kills only the direct child, orphaning vault_doctor's own
    AI workers; run_with_pgkill takes down the whole process group.
    """
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "pending_summaries.jsonl").write_text("", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_pgkill(cmd: list[str], **kwargs: object):
        calls.append(cmd)
        return "ok", types.SimpleNamespace(
            returncode=0, stdout="doctor output\n", stderr=""
        )

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("doctor sub-run must not use subprocess.run")

    monkeypatch.setattr(mod, "run_with_pgkill", _fake_pgkill)
    monkeypatch.setattr(mod.subprocess, "run", _boom)
    monkeypatch.setattr(mod.vault_common, "resolve_vault", lambda **_: vault)
    monkeypatch.setattr(
        sys,
        "argv",
        ["summarize_sessions.py", "--vault", str(vault), "--run-doctor"],
    )

    mod.main()

    assert len(calls) == 1
    assert Path(calls[0][1]).name == "vault_doctor.py"
    assert calls[0][2:] == ["--fix-all"]
    assert "doctor output" in capsys.readouterr().out


def test_run_doctor_timeout_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "pending_summaries.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(mod, "run_with_pgkill", lambda *_a, **_k: ("timeout", None))
    monkeypatch.setattr(mod.vault_common, "resolve_vault", lambda **_: vault)
    monkeypatch.setattr(
        sys,
        "argv",
        ["summarize_sessions.py", "--vault", str(vault), "--run-doctor"],
    )

    mod.main()  # must not raise

    assert "timed out after 600s" in capsys.readouterr().err
