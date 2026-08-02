from __future__ import annotations

import asyncio
import importlib
import json
import sys
import types
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SUMMARIZE_SESSIONS_PATH = SCRIPTS_DIR / "summarize_sessions.py"


def test_summarize_sessions_source_uses_ai_backend_not_claude_agent_sdk() -> None:
    source = SUMMARIZE_SESSIONS_PATH.read_text(encoding="utf-8")

    assert "claude-agent-sdk" not in source
    assert "claude_agent_sdk" not in source
    assert "import ai_backend" in source


def test_summarizer_config_models_accept_none() -> None:
    import vault_config

    assert vault_config._CONFIG_SCHEMA["summarizer"]["model"] == (str, type(None))
    assert vault_config._CONFIG_SCHEMA["summarizer"]["cluster_model"] == (
        str,
        type(None),
    )


class _FakeSemaphore:
    def __init__(self, _: int = 1) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


def _fresh_summarize_sessions(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    monkeypatch.setitem(
        sys.modules,
        "anyio",
        types.SimpleNamespace(
            Semaphore=_FakeSemaphore,
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


def _transcript_module() -> types.ModuleType:
    """Return ``summarizer.transcript`` for monkeypatching (QA-003).

    The hierarchical preprocessors (``preprocess_transcript_hierarchical``,
    ``_summarize_chunk``) moved out of the entry shim into ``summarizer.transcript``;
    their bare-name dependencies resolve there, so patches must target that module.
    Imported lazily because ``summarizer.transcript`` pulls in ``summarizer.prompt``
    → ``anyio``, which is only present once ``_fresh_summarize_sessions`` has
    installed its stub.
    """
    import summarizer.transcript

    return summarizer.transcript


def _pipeline_module() -> types.ModuleType:
    """Return ``summarizer.pipeline`` for monkeypatching (QA-003).

    ``summarize_one`` and its stage helpers (_early_gate / _apply_merge_decision /
    _handle_write_gate_decision / _apply_backlinks_and_strip_links) moved out of
    the entry shim into ``summarizer.pipeline``; their bare-name dependencies
    (preprocess / prompt runner / dedup / build_prompt / _ACTIVE_SESSION_GRACE_SECS)
    resolve there, so patches must target that module. Imported lazily — safe
    after ``_fresh_summarize_sessions`` has installed its anyio stub.
    """
    import summarizer.pipeline

    return summarizer.pipeline


def test_run_summarizer_prompt_delegates_to_ai_backend_in_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    thread_calls: list[object] = []

    async def fake_run_sync(func: object, *args: object) -> object:
        thread_calls.append(func)
        assert callable(func)
        return func(*args)

    monkeypatch.setitem(
        sys.modules,
        "anyio",
        types.SimpleNamespace(
            Semaphore=object,
            to_thread=types.SimpleNamespace(run_sync=fake_run_sync),
        ),
    )
    sys.modules.pop("summarize_sessions", None)
    # QA-003: _run_summarizer_prompt now lives in summarizer.prompt, which is
    # imported once and cached (unlike the shim, re-imported per test). Pop it
    # so this test's custom to_thread stub is picked up on the fresh import.
    sys.modules.pop("summarizer.prompt", None)
    summarize_sessions = importlib.import_module("summarize_sessions")
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "summary text"

    monkeypatch.setattr(
        summarize_sessions.ai_backend, "run_ai_prompt", fake_run_ai_prompt
    )

    result = asyncio.run(
        summarize_sessions._run_summarizer_prompt(
            "prompt text",
            model="model-id",
            model_tier="large",
            purpose="session-summary",
            timeout=123,
            vault=tmp_path,
        )
    )

    assert result == "summary text"
    assert len(thread_calls) == 1
    assert calls == [
        {
            "prompt": "prompt text",
            "model": "model-id",
            "model_tier": "large",
            "purpose": "session-summary",
            "timeout": 123,
            "vault": tmp_path,
        }
    ]


def test_build_prompt_loads_template_and_substitutes_placeholders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARC-029: build_prompt loads note_writing.txt as a string.Template and
    substitutes every placeholder. No ``$token`` should leak through to the
    rendered prompt, and the dynamically-built tag/dedup blocks must appear."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Empty similar_notes -> no dedup block; existing_tags -> "STRONGLY prefer" branch.
    prompt = summarize_sessions.build_prompt(
        project="parsidion",
        categories=["testing", "refactor"],
        cleaned_transcript="transcript body goes here",
        existing_tags=["python", "hook"],
        session_id="abc-123",
        similar_notes=None,
    )
    # All template substitutions resolved — no leftover $placeholders.
    assert "$" not in prompt, f"unfilled template placeholder: {prompt}"
    # Static anchor lines from the template survived intact.
    assert "SYSTEM: You are a vault-note-writing API." in prompt
    assert "Project: parsidion" in prompt
    assert "session_id: abc-123" in prompt
    # ARC-010: valid_types interpolated from the _VALID_NOTE_TYPES constant,
    # so 'knowledge' must appear (it was previously missing — see ARC-010).
    assert "knowledge" in prompt
    # ARC-029: tag-rules instruction came from _render_tags_instruction; the
    # STRONGLY-prefer branch fires when existing_tags is non-empty.
    assert "STRONGLY prefer existing tags: python, hook" in prompt
    # The shared kebab-case rule is present in BOTH branches via the helper.
    assert "NEVER use underscores — always kebab-case" in prompt
    # No dedup_block when similar_notes is None/empty.
    assert "IMPORTANT: The following existing vault notes" not in prompt


def test_build_prompt_renders_dedup_block_when_similar_notes_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARC-029: the dedup block (now built by _render_dedup_block) still
    injects correctly when similar_notes is provided."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    similar = [("existing-note", 0.91, "summary of existing note")]
    prompt = summarize_sessions.build_prompt(
        project="parsidion",
        categories=["x"],
        cleaned_transcript="body",
        existing_tags=[],
        session_id="sid-1",
        similar_notes=similar,
    )
    assert "IMPORTANT: The following existing vault notes" in prompt
    assert "[[existing-note]] (similarity 0.91)" in prompt
    # JSON example with literal braces is preserved verbatim (string.Template).
    assert '"decision": "merge"' in prompt


def test_load_prompt_template_caches_per_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARC-029: _load_prompt_template caches the parsed Template so a
    summarizer run with N entries reads each prompt file once."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Clear any cache inherited from an earlier import.
    summarize_sessions._PROMPT_TEMPLATE_CACHE.clear()
    a1 = summarize_sessions._load_prompt_template("note_writing.txt")
    a2 = summarize_sessions._load_prompt_template("note_writing.txt")
    assert a1 is a2, "template not cached across calls"


def test_summarize_chunk_uses_small_tier_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    calls: list[dict[str, object]] = []

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "backend summary"

    def fake_get_config(section: str, key: str, default: object = None) -> object:
        assert (section, key, default) == ("summarizer", "ai_timeout", None)
        return 42

    monkeypatch.setattr(
        _transcript_module(),
        "_run_summarizer_prompt",
        fake_run_summarizer_prompt,
    )
    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)

    result = asyncio.run(
        summarize_sessions._summarize_chunk(
            "chunk body", 2, 3, model=None, vault=tmp_path
        )
    )

    assert result == "backend summary"
    assert len(calls) == 1
    assert "portion (2/3)" in str(calls[0]["prompt"])
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "summarizer-chunk"
    assert calls[0]["timeout"] == 42
    assert calls[0]["vault"] == tmp_path


def test_summarize_chunk_falls_back_to_first_500_chars_on_backend_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    chunk_text = "x" * 600

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(
        _transcript_module(),
        "_run_summarizer_prompt",
        fake_run_summarizer_prompt,
    )

    result = asyncio.run(
        summarize_sessions._summarize_chunk(
            chunk_text, 1, 1, model="chunk-model", vault=tmp_path
        )
    )

    assert result == chunk_text[:500]


def test_preprocess_transcript_hierarchical_passes_vault_to_chunk_summarizer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    calls: list[dict[str, object]] = []

    def fake_preprocess_transcript(
        transcript_path_str: str,
        tail_lines: int,
        max_chars: int | None,
        tail_bytes: int | None,
    ) -> str:
        return "line\n" * 10

    async def fake_summarize_chunk(
        chunk_text: str,
        chunk_num: int,
        total_chunks: int,
        model: str | None,
        vault: Path,
    ) -> str:
        calls.append(
            {
                "chunk_text": chunk_text,
                "chunk_num": chunk_num,
                "total_chunks": total_chunks,
                "model": model,
                "vault": vault,
            }
        )
        return f"summary {chunk_num}"

    monkeypatch.setattr(
        _transcript_module(),
        "preprocess_transcript",
        fake_preprocess_transcript,
    )
    monkeypatch.setattr(_transcript_module(), "_summarize_chunk", fake_summarize_chunk)

    result = asyncio.run(
        summarize_sessions.preprocess_transcript_hierarchical(
            "session.jsonl",
            tail_lines=400,
            max_cleaned_chars=12,
            cluster_model=None,
            vault=tmp_path,
        )
    )

    assert result.startswith("[Hierarchical summary from ")
    assert calls
    assert {call["vault"] for call in calls} == {tmp_path}
    assert {call["model"] for call in calls} == {None}


def test_preprocess_transcript_hierarchical_chunks_real_oversized_transcripts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                '{"type":"user","content":"' + ("u" * 120) + '"}',
                '{"type":"assistant","content":"' + ("a" * 120) + '"}',
                '{"type":"user","content":"' + ("v" * 120) + '"}',
                '{"type":"assistant","content":"' + ("b" * 120) + '"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    async def fake_summarize_chunk(
        chunk_text: str,
        chunk_num: int,
        total_chunks: int,
        model: str | None,
        vault: Path,
    ) -> str:
        calls.append(
            {
                "chunk_text": chunk_text,
                "chunk_num": chunk_num,
                "total_chunks": total_chunks,
                "model": model,
                "vault": vault,
            }
        )
        return f"summary {chunk_num}"

    monkeypatch.setattr(_transcript_module(), "_summarize_chunk", fake_summarize_chunk)

    result = asyncio.run(
        summarize_sessions.preprocess_transcript_hierarchical(
            str(transcript_path),
            tail_lines=400,
            max_cleaned_chars=100,
            cluster_model=None,
            vault=tmp_path,
        )
    )

    assert result.startswith("[Hierarchical summary from ")
    assert len(calls) > 1
    assert {call["vault"] for call in calls} == {tmp_path}


def test_summarize_one_uses_large_tier_backend_with_configured_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"assistant","message":{"content":"fixed bug"}}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    calls: list[dict[str, object]] = []

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return (
            "---\n"
            "date: 2026-04-27\n"
            "type: debugging\n"
            "tags:\n"
            "  - debugging\n"
            "confidence: high\n"
            "---\n"
            "# Test Note\n\nUseful note."
        )

    def fake_get_config(section: str, key: str, default: object = None) -> object:
        if (section, key, default) == ("summarizer", "ai_timeout", None):
            return 77
        if (section, key) == ("summarizer", "dedup_threshold"):
            return default
        raise AssertionError((section, key, default))

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )

    entry = {
        "transcript_path": str(transcript_path),
        "project": "parsidion",
        "categories": ["error_fix"],
        "session_id": "session-1234",
    }

    result_entry, written = asyncio.run(
        summarize_sessions.summarize_one(
            entry,
            None,
            True,
            summarize_sessions.anyio.Semaphore(1),
            ["debugging"],
            False,
            vault,
            cluster_model=None,
        )
    )

    assert result_entry == entry
    assert written is None
    assert len(calls) == 1
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "large"
    assert calls[0]["purpose"] == "summarizer-note"
    assert calls[0]["timeout"] == 77
    assert calls[0]["vault"] == vault


def test_summarize_one_preserves_skip_write_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"user","content":"Investigate a routine issue"}\n'
        '{"type":"assistant","content":"Ran checks and found nothing reusable"}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    calls: list[dict[str, object]] = []

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return '{"decision": "skip", "reason": "routine transient session"}'

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )

    async def run() -> tuple[dict[str, object], Path | str | None]:
        return await summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": ["testing"],
                "session_id": "session-1234",
            },
            "summary-model",
            False,
            summarize_sessions.anyio.Semaphore(1),
            ["testing"],
            False,
            vault,
            cluster_model=None,
        )

    entry, written = asyncio.run(run())

    assert entry["session_id"] == "session-1234"
    assert written == summarize_sessions._SKIPPED
    assert len(calls) == 1
    assert calls[0]["model"] == "summary-model"
    assert "session-1234" in str(calls[0]["prompt"])


def test_summarize_one_defers_active_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A transcript still being written (mtime within the grace window) is
    deferred — left in the queue untouched — rather than summarized mid-flight."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Re-enable the guard (the shared helper disables it for unit tests).
    monkeypatch.setattr(_pipeline_module(), "_ACTIVE_SESSION_GRACE_SECS", 120)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"user","content":"in progress"}\n', encoding="utf-8"
    )
    vault = tmp_path / "vault"
    vault.mkdir()

    async def run() -> tuple[dict[str, object], Path | str | None]:
        return await summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": [],
                "session_id": "session-active",
            },
            None,
            False,
            summarize_sessions.anyio.Semaphore(1),
            [],
            False,
            vault,
            cluster_model=None,
        )

    _entry, written = asyncio.run(run())
    assert written == summarize_sessions._DEFERRED


def test_summarize_one_preserves_skip_write_gate_when_fenced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fenced JSON skip decision must still be recognized as a write-gate skip.

    Regression: the backend wraps write-gate JSON in a ```json code fence; without
    _strip_code_fence the decision starts with a backtick, misses the JSON branch,
    falls through to write_note, and fails frontmatter validation (false "failed").
    """
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"user","content":"Investigate a routine issue"}\n'
        '{"type":"assistant","content":"Nothing reusable here"}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        return (
            '```json\n{"decision": "skip", "reason": "routine transient session"}\n```'
        )

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )

    async def run() -> tuple[dict[str, object], Path | str | None]:
        return await summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": ["testing"],
                "session_id": "session-fenced",
            },
            "summary-model",
            False,
            summarize_sessions.anyio.Semaphore(1),
            ["testing"],
            False,
            vault,
            cluster_model=None,
        )

    entry, written = asyncio.run(run())

    assert entry["session_id"] == "session-fenced"
    assert written == summarize_sessions._SKIPPED


def test_summarize_one_preserves_dry_run_markdown_note_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"assistant","message":{"content":"fixed bug"}}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    vault.mkdir()

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        return (
            "---\n"
            "date: 2026-04-27\n"
            "type: debugging\n"
            "tags:\n"
            "  - debugging\n"
            "confidence: high\n"
            "---\n"
            "# Test Note\n\nUseful note."
        )

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )

    _entry, written = asyncio.run(
        summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": ["testing"],
                "session_id": "session-1234",
            },
            "summary-model",
            True,
            summarize_sessions.anyio.Semaphore(1),
            ["testing"],
            False,
            vault,
            cluster_model=None,
        )
    )

    captured = capsys.readouterr()
    assert written is None
    assert "[dry-run] Would write:" in captured.out
    assert "Debugging/test-note.md" in captured.out


def test_main_uses_backend_defaults_when_summarizer_models_are_null(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text("", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    observed: dict[str, object] = {}

    def fake_get_config(section: str, key: str, default: object = None) -> object:
        if section == "summarizer" and key in {"model", "cluster_model"}:
            assert default is None
            return None
        return default

    def fake_read_pending(path: Path) -> list[dict[str, object]]:
        assert path == sessions
        return [
            {
                "session_id": "s",
                "transcript_path": str(tmp_path / "t.jsonl"),
                "project": "p",
                "categories": ["research"],
            }
        ]

    async def fake_run_all(
        entries: list[dict[str, object]],
        model: str | None,
        dry_run: bool,
        persist: bool,
        vault_path: Path,
        max_parallel: int,
        tail_lines: int,
        tail_bytes: int | None,
        max_cleaned_chars: int,
        cluster_model: str | None,
    ) -> list[tuple[dict[str, object], Path | str | None]]:
        observed.update(
            {
                "model": model,
                "cluster_model": cluster_model,
                "dry_run": dry_run,
                "vault_path": vault_path,
                "max_parallel": max_parallel,
                "tail_lines": tail_lines,
                "max_cleaned_chars": max_cleaned_chars,
            }
        )
        return [(entries[0], None)]

    def fake_anyio_run(
        func: Callable[..., Coroutine[Any, Any, object]], *args: object
    ) -> object:
        return asyncio.run(func(*args))

    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)
    monkeypatch.setattr(
        summarize_sessions.vault_common, "resolve_vault", lambda **_: vault
    )
    monkeypatch.setattr(
        summarize_sessions.vault_common,
        "apply_configured_env_defaults",
        lambda **_: None,
    )
    monkeypatch.setattr(summarize_sessions, "read_pending", fake_read_pending)
    monkeypatch.setattr(summarize_sessions, "run_all", fake_run_all)
    monkeypatch.setattr(summarize_sessions.anyio, "run", fake_anyio_run, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_sessions.py",
            "--sessions",
            str(sessions),
            "--vault",
            str(vault),
            "--dry-run",
        ],
    )

    summarize_sessions.main()

    captured = capsys.readouterr()
    assert observed["model"] is None
    assert observed["cluster_model"] is None
    assert "backend large default" in captured.out


def test_main_removes_write_gate_skips_from_default_pending_queue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    pending = vault / "pending_summaries.jsonl"
    pending.write_text("{}\n", encoding="utf-8")
    entry = {
        "session_id": "skip-session",
        "transcript_path": str(tmp_path / "t.jsonl"),
        "project": "p",
        "categories": ["research"],
    }
    removed: list[dict[str, object]] = []

    def fake_get_config(section: str, key: str, default: object = None) -> object:
        return default

    def fake_read_pending(path: Path) -> list[dict[str, object]]:
        assert path == pending
        return [entry]

    async def fake_run_all(
        entries: list[dict[str, object]],
        model: str | None,
        dry_run: bool,
        persist: bool,
        vault_path: Path,
        max_parallel: int,
        tail_lines: int,
        tail_bytes: int | None,
        max_cleaned_chars: int,
        cluster_model: str | None,
    ) -> list[tuple[dict[str, object], Path | str | None]]:
        return [(entries[0], summarize_sessions._SKIPPED)]

    def fake_remove_processed(
        pending_path: Path, processed_entries: list[dict[str, object]]
    ) -> None:
        assert pending_path == pending
        removed.extend(processed_entries)

    def fake_anyio_run(
        func: Callable[..., Coroutine[Any, Any, object]], *args: object
    ) -> object:
        return asyncio.run(func(*args))

    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)
    monkeypatch.setattr(
        summarize_sessions.vault_common, "resolve_vault", lambda **_: vault
    )
    monkeypatch.setattr(
        summarize_sessions.vault_common,
        "apply_configured_env_defaults",
        lambda **_: None,
    )
    monkeypatch.setattr(summarize_sessions, "read_pending", fake_read_pending)
    monkeypatch.setattr(summarize_sessions, "run_all", fake_run_all)
    monkeypatch.setattr(summarize_sessions, "remove_processed", fake_remove_processed)
    monkeypatch.setattr(summarize_sessions.anyio, "run", fake_anyio_run, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["summarize_sessions.py", "--vault", str(vault)],
    )

    summarize_sessions.main()

    captured = capsys.readouterr()
    assert removed == [entry]
    assert "1 skipped by write-gate" in captured.out
    assert "failed" not in captured.out


def test_main_cli_model_overrides_large_model_while_cluster_uses_backend_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text("", encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_get_config(section: str, key: str, default: object = None) -> object:
        if section == "summarizer" and key == "model":
            return "configured-large-model"
        if section == "summarizer" and key == "cluster_model":
            assert default is None
            return None
        return default

    def fake_read_pending(path: Path) -> list[dict[str, object]]:
        return [
            {
                "session_id": "s",
                "transcript_path": str(tmp_path / "t.jsonl"),
                "project": "p",
                "categories": ["research"],
            }
        ]

    async def fake_run_all(
        entries: list[dict[str, object]],
        model: str | None,
        dry_run: bool,
        persist: bool,
        vault_path: Path,
        max_parallel: int,
        tail_lines: int,
        tail_bytes: int | None,
        max_cleaned_chars: int,
        cluster_model: str | None,
    ) -> list[tuple[dict[str, object], Path | str | None]]:
        observed["model"] = model
        observed["cluster_model"] = cluster_model
        return [(entries[0], None)]

    def fake_anyio_run(
        func: Callable[..., Coroutine[Any, Any, object]], *args: object
    ) -> object:
        return asyncio.run(func(*args))

    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)
    monkeypatch.setattr(
        summarize_sessions.vault_common, "resolve_vault", lambda **_: tmp_path / "vault"
    )
    monkeypatch.setattr(
        summarize_sessions.vault_common,
        "apply_configured_env_defaults",
        lambda **_: None,
    )
    monkeypatch.setattr(summarize_sessions, "read_pending", fake_read_pending)
    monkeypatch.setattr(summarize_sessions, "run_all", fake_run_all)
    monkeypatch.setattr(summarize_sessions.anyio, "run", fake_anyio_run, raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "summarize_sessions.py",
            "--sessions",
            str(sessions),
            "--dry-run",
            "--model",
            "cli-large-model",
        ],
    )

    summarize_sessions.main()

    assert observed["model"] == "cli-large-model"
    assert observed["cluster_model"] is None


def test_ensure_closing_frontmatter_delimiter_inserts_missing_closer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # The model emitted the opening '---' and all fields but no closing '---'.
    note = (
        "---\n"
        "date: 2026-06-14\n"
        "type: pattern\n"
        "tags: [python, architecture]\n"
        "project: parsidion\n"
        "confidence: high\n"
        "sources: []\n"
        "related: ['[[parsidion]]']\n"
        "session_id: abc123\n"
        "\n"
        "# Vault Library Importability Pattern\n\n"
        "Some reusable insight.\n"
    )

    repaired = summarize_sessions._ensure_closing_frontmatter_delimiter(note)

    assert repaired != note
    # Closing delimiter inserted before the body (first blank line).
    assert "\nsession_id: abc123\n---\n\n#" in repaired
    fm = summarize_sessions.vault_common.parse_frontmatter(repaired)
    assert fm.get("type") == "pattern"
    assert fm.get("date") == "2026-06-14"


def test_ensure_closing_frontmatter_delimiter_noop_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Already well-formed (both delimiters present) — unchanged.
    well_formed = (
        "---\n"
        "date: 2026-06-14\n"
        "type: debugging\n"
        "tags:\n"
        "  - debugging\n"
        "---\n"
        "# Note\n\nBody.\n"
    )
    assert (
        summarize_sessions._ensure_closing_frontmatter_delimiter(well_formed)
        == well_formed
    )
    # No opening '---' at all — unchanged (validator will reject).
    no_opening = "# Just a heading\n\nNo frontmatter here.\n"
    assert (
        summarize_sessions._ensure_closing_frontmatter_delimiter(no_opening)
        == no_opening
    )


def test_write_note_salvages_note_missing_closing_frontmatter_delimiter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    # Realistic model output that omits the closing '---'.
    note = (
        "---\n"
        "date: 2026-04-27\n"
        "type: debugging\n"
        "tags:\n"
        "  - debugging\n"
        "confidence: high\n"
        "\n"
        "# Test Note\n\nUseful note.\n"
    )

    result = summarize_sessions.write_note(note, True, vault)

    captured = capsys.readouterr()
    assert result is None  # dry-run returns None
    assert "[dry-run] Would write:" in captured.out
    assert "Debugging/test-note.md" in captured.out
    assert "Refusing to write note" not in captured.err


def test_strip_leading_preamble_strips_prose_before_frontmatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # The model prefaced the note with prose despite the "no preamble" instruction.
    note = (
        "Here is the vault note for this session:\n\n"
        "---\n"
        "date: 2026-06-27\n"
        "type: debugging\n"
        "tags: [python]\n"
        "confidence: high\n"
        "---\n\n"
        "# Some Note\n\nUseful insight.\n"
    )

    stripped = summarize_sessions._strip_leading_preamble(note)

    assert stripped != note
    assert stripped.startswith("---")
    fm = summarize_sessions.vault_common.parse_frontmatter(stripped)
    assert fm.get("type") == "debugging"
    assert fm.get("date") == "2026-06-27"


def test_strip_leading_preamble_noop_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Already starts with '---' — unchanged.
    well_formed = (
        "---\ndate: 2026-06-27\ntype: pattern\ntags: [x]\n---\n# Note\n\nBody.\n"
    )
    assert summarize_sessions._strip_leading_preamble(well_formed) == well_formed
    # No frontmatter at all — unchanged (validator will reject).
    prose_only = "Just some prose with no delimiters.\n"
    assert summarize_sessions._strip_leading_preamble(prose_only) == prose_only
    # A body horizontal rule must NOT be mistaken for a frontmatter delimiter.
    body_rule = "Intro prose.\n\n---\n\nJust a paragraph, not frontmatter.\n"
    assert summarize_sessions._strip_leading_preamble(body_rule) == body_rule


def test_write_note_salvages_note_with_leading_preamble(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    # Realistic model output that prefaced the note with prose.
    note = (
        "Sure — here's the note:\n\n"
        "---\n"
        "date: 2026-06-27\n"
        "type: debugging\n"
        "tags:\n"
        "  - debugging\n"
        "confidence: high\n"
        "---\n\n"
        "# Preamble Note\n\nUseful insight.\n"
    )

    result = summarize_sessions.write_note(note, True, vault)

    captured = capsys.readouterr()
    assert result is None  # dry-run returns None
    assert "[dry-run] Would write:" in captured.out
    assert "Debugging/preamble-note.md" in captured.out
    assert "Refusing to write note" not in captured.err


def test_note_body_strips_frontmatter(monkeypatch: pytest.MonkeyPatch) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    with_fm = (
        "---\ndate: 2026-06-15\ntype: pattern\ntags: [x]\n---\n\n# Title\n\nBody.\n"
    )
    assert summarize_sessions._note_body(with_fm) == "# Title\n\nBody."
    # No frontmatter -> returned stripped, as-is.
    assert summarize_sessions._note_body("# Just a heading\n") == "# Just a heading"


def test_write_note_merges_on_slug_collision_no_sibling(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """On slug collision, write_note merges into the existing note — no -HHMM sibling."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    debugging = vault / "Debugging"
    debugging.mkdir(parents=True)
    existing = (
        "---\n"
        "date: 2026-06-01\n"
        "type: debugging\n"
        "tags: [debugging]\n"
        "---\n"
        "# Test Note\n\nOriginal insight.\n"
    )
    (debugging / "test-note.md").write_text(existing, encoding="utf-8")

    new_note = (
        "---\n"
        "date: 2026-06-15\n"
        "type: debugging\n"
        "tags: [debugging]\n"
        "---\n"
        "# Test Note\n\nFollow-up insight.\n"
    )
    result = summarize_sessions.write_note(new_note, False, vault)

    assert result is not None
    assert result.name == "test-note.md"  # same file, not a sibling
    # No timestamped sibling was created.
    assert [p.name for p in debugging.iterdir()] == ["test-note.md"]
    # Existing content preserved + new body appended under a Session update heading.
    content = result.read_text(encoding="utf-8")
    assert "Original insight." in content
    assert "Follow-up insight." in content
    assert "## Session update" in content
    captured = capsys.readouterr()
    assert "Slug collision" in captured.err


def test_normalize_related_field_clean_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    note = (
        "---\ndate: 2026-06-16\ntype: pattern\ntags: [x]\n"
        'related: ["[[a]]", "[[b]]"]\n---\n# T\n\nBody.\n'
    )
    assert summarize_sessions._normalize_related_field(note) == note


def test_normalize_related_field_preserves_folder_qualified_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # A folder-qualified wikilink must NOT be truncated at the '/'.
    note = (
        "---\ndate: 2026-06-16\ntype: pattern\ntags: [x]\n"
        'related: ["[[yes-man/settings-path-api-drift]]"]\n---\n# T\n\nBody.\n'
    )
    out = summarize_sessions._normalize_related_field(note)
    assert "[[yes-man/settings-path-api-drift]]" in out


def test_normalize_related_field_repairs_malformations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Real corruption patterns observed from a dedup run:
    #  - "["parsidion"]"     (quoted single-bracket)
    #  - "[[real-note]]"     (clean)
    #  - "[["other-note"]]"  (quoted inside double brackets)
    note = (
        "---\ndate: 2026-06-16\ntype: pattern\ntags: [x]\n"
        'related: ["["parsidion"]", "[[real-note]]", "[["other-note"]]"]\n'
        "---\n# T\n\nBody.\n"
    )
    out = summarize_sessions._normalize_related_field(note)
    m = summarize_sessions._RELATED_LINE_RE.search(out)
    assert m.group(1) == '["[[parsidion]]", "[[real-note]]", "[[other-note]]"]'


def test_normalize_related_field_empty_when_only_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    # Bare prose with spaces (no bracket-wrapped stem) yields nothing.
    note = (
        "---\ndate: 2026-06-16\ntype: pattern\ntags: [x]\n"
        'related: ["not a wikilink here", "plain prose text"]\n---\n# T\n\nBody.\n'
    )
    out = summarize_sessions._normalize_related_field(note)
    assert "related: []" in out


def test_write_note_normalizes_malformed_related(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()
    # AI emitted a [["stem"]] malformation (quoted inside double brackets).
    note = (
        "---\ndate: 2026-06-16\ntype: debugging\ntags: [debugging]\n"
        'related: [["real-note"], "[[other-note]]"]\n'
        "---\n# Fresh Note\n\nBody.\n"
    )
    result = summarize_sessions.write_note(note, False, vault)
    assert result is not None
    written = result.read_text(encoding="utf-8")
    # Malformed entries repaired to clean [[wikilinks]].
    assert 'related: ["[[real-note]]", "[[other-note]]"]' in written
    assert '[["real-note"]' not in written


def test_prune_dead_letters_respects_retention_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    from datetime import datetime, timedelta

    now = datetime.now()
    entries = [
        {
            "session_id": "old",
            "last_failure": "no result",
            "dead_lettered_at": (now - timedelta(days=30)).isoformat(),
        },
        {
            "session_id": "recent",
            "last_failure": "no result",
            "dead_lettered_at": now.isoformat(),
        },
        {"session_id": "undated", "last_failure": "no result"},  # no ts -> kept
        {  # unparseable ts -> kept
            "session_id": "badts",
            "last_failure": "no result",
            "dead_lettered_at": "not-a-date",
        },
    ]
    dl = tmp_path / "dead_letters.jsonl"
    dl.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    pruned = summarize_sessions._prune_dead_letters(tmp_path, 7)
    assert pruned == 1  # only the 30-day-old entry

    remaining = {
        json.loads(line)["session_id"]
        for line in dl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert remaining == {"recent", "undated", "badts"}


def test_prune_dead_letters_disabled_and_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    from datetime import datetime, timedelta

    now = datetime.now()
    dl = tmp_path / "dead_letters.jsonl"
    dl.write_text(
        json.dumps(
            {
                "session_id": "ancient",
                "dead_lettered_at": (now - timedelta(days=99)).isoformat(),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # retention_days <= 0 disables pruning entirely.
    assert summarize_sessions._prune_dead_letters(tmp_path, 0) == 0
    assert "ancient" in dl.read_text(encoding="utf-8")

    # Missing file is a safe no-op.
    assert summarize_sessions._prune_dead_letters(tmp_path / "nope", 7) == 0


# --- ARC-010: knowledge type parity ----------------------------------------


def test_arc010_valid_note_types_match_vault_doctor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARC-010 guard: summarizer type enum must match vault_doctor's.

    Without this assertion the two constants drift silently -- exactly the
    original bug where `knowledge` was missing from the summarizer but
    present everywhere else.
    """
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    import vault_doctor

    assert set(summarize_sessions._VALID_NOTE_TYPES) == set(vault_doctor.VALID_TYPES), (
        f"summarizer types: {sorted(summarize_sessions._VALID_NOTE_TYPES)}\n"
        f"vault_doctor types: {sorted(vault_doctor.VALID_TYPES)}\n"
        "These constants must agree so a `type: knowledge` model response is "
        "validated identically on both sides."
    )


def test_arc010_type_folders_cover_every_valid_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every valid note type must route to a vault folder."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    missing = sorted(
        t
        for t in summarize_sessions._VALID_NOTE_TYPES
        if t not in summarize_sessions._TYPE_FOLDERS
    )
    assert not missing, (
        f"_VALID_NOTE_TYPES entries missing from _TYPE_FOLDERS: {missing}"
    )


def test_arc010_knowledge_type_routes_to_knowledge_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A note with `type: knowledge` must route to Knowledge/."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    assert summarize_sessions._TYPE_FOLDERS["knowledge"] == "Knowledge"

    vault = tmp_path / "vault"
    vault.mkdir()
    note = (
        "---\n"
        "date: 2026-07-28\n"
        "type: knowledge\n"
        "tags: [arc-010]\n"
        'related: ["[[some-note]]"]\n'
        "---\n\n"
        "# Knowledge Note\n\n## Summary\nRoutes correctly.\n"
    )
    written = summarize_sessions.write_note(
        note, dry_run=False, vault=vault, project="arc-010", categories=[]
    )
    assert written is not None
    assert written.parent.name == "Knowledge"
    assert written.exists()


def test_arc010_prompt_lists_knowledge_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """The prompt's type list must include `knowledge` and be interpolated."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    prompt = summarize_sessions.build_prompt(
        project="proj",
        categories=["general"],
        cleaned_transcript="Human: did a thing",
        existing_tags=[],
        session_id="abc-123",
    )
    # The prompt must enumerate every valid type — including knowledge — so
    # the model can emit one. The interpolation must come from the constant.
    assert "knowledge" in prompt
    for t in summarize_sessions._VALID_NOTE_TYPES:
        assert t in prompt, f"prompt missing type {t!r}"


# --- ARC-012: task-group boundary isolates failures -------------------------


def test_arc012_one_raising_summarize_one_does_not_kill_siblings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ARC-012: an unhandled exception inside summarize_one must not cancel
    its siblings via anyio.create_task_group()'s cancel-on-raise semantics.

    Regression for the original bug: an unguarded write path raised inside
    one task and the task group cancelled every sibling, leaving the queue
    uncleaned and the index not rebuilt. The test fakes three entries whose
    summarize_one raises/returns in turn and confirms all three still appear
    in the results list with the failing one mapped to the failure sentinel.

    Skipped when ``anyio`` is not installed in the test environment (it is a
    PEP 723 inline dependency, not declared under the test extras). The bug
    only manifests with a real task group; the sequential fake used by other
    tests does not propagate cancellation, so it cannot reproduce the issue.
    """
    real_anyio = pytest.importorskip("anyio")  # PEP 723 inline dep; skip if absent

    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    vault = tmp_path / "vault"
    vault.mkdir()

    entries: list[dict[str, object]] = [
        {"session_id": "ok-1", "project": "p1"},
        {"session_id": "boom", "project": "p1"},
        {"session_id": "ok-2", "project": "p2"},
    ]
    written_path = tmp_path / "note.md"
    written_path.write_text("# x\n", encoding="utf-8")

    async def fake_summarize_one(entry, *args, **kwargs):  # type: ignore[no-untyped-def]
        sid = entry.get("session_id")
        if sid == "boom":
            raise RuntimeError("simulated unguarded write failure")
        return entry, written_path

    # Restore the real anyio and reload the summarizer so its module-level
    # ``anyio`` references resolve to the real package, not the stub.
    monkeypatch.setitem(sys.modules, "anyio", real_anyio)
    sys.modules.pop("summarize_sessions", None)
    summarize_sessions = importlib.import_module("summarize_sessions")
    monkeypatch.setattr(summarize_sessions, "summarize_one", fake_summarize_one)
    monkeypatch.setattr(summarize_sessions, "_ACTIVE_SESSION_GRACE_SECS", 0)
    monkeypatch.setattr(
        summarize_sessions.vault_common,
        "all_vault_notes",
        lambda *a, **kw: [],
    )
    monkeypatch.setattr(summarize_sessions, "read_existing_tags", lambda v: [])
    monkeypatch.setattr(summarize_sessions, "read_project_names", lambda **kw: set())
    monkeypatch.setattr(summarize_sessions, "_write_progress", lambda **kw: None)

    results = real_anyio.run(
        summarize_sessions.run_all,
        entries,
        None,  # model
        True,  # dry_run
        False,  # persist
        vault,
        2,  # max_parallel
        400,  # tail_lines
        262_144,  # tail_bytes
        12_000,  # max_cleaned_chars
        None,  # cluster_model
    )

    # All three entries appear in results; the raising one became (entry, None).
    sids = [r[0].get("session_id") for r in results]
    assert sorted(sids) == ["boom", "ok-1", "ok-2"]
    by_sid = {r[0].get("session_id"): r[1] for r in results}
    assert by_sid["ok-1"] == written_path
    assert by_sid["ok-2"] == written_path
    assert by_sid["boom"] is None, "raising entry must map to failure sentinel"

    captured = capsys.readouterr()
    assert "Unhandled failure" in captured.err
    # _mark_failure must have set the failure-reason key so the dead-letter
    # warning at remove_processed time can report the real reason.
    boom_entry = next(r[0] for r in results if r[0].get("session_id") == "boom")
    assert boom_entry.get(summarize_sessions._FAILURE_REASON_KEY), (
        "_mark_failure did not fire on the unhandled exception"
    )


# ---------------------------------------------------------------------------
# SEC-107: merge path must validate AI-generated content + back up the target
# ---------------------------------------------------------------------------


def test_summarize_one_merge_rejects_invalid_frontmatter_leaves_target_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SEC-107: an AI merge decision whose ``new_content`` has invalid
    frontmatter must NOT overwrite the target note — the existing file is
    left byte-identical and the entry is marked failed (not merged)."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"user","content":"Investigate prior insight"}\n'
        '{"type":"assistant","content":"Follow up"}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    debugging = vault / "Debugging"
    debugging.mkdir(parents=True)
    original = (
        "---\n"
        "date: 2026-06-01\n"
        "type: debugging\n"
        "tags: [debugging]\n"
        "---\n"
        "# Real Note\n\nTrusted content.\n"
    )
    target = debugging / "real-note.md"
    target.write_text(original, encoding="utf-8")

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        # Malicious/corrupted merge decision: new_content lacks required fields
        # and would corrupt the trusted note if written unchecked.
        return json.dumps(
            {
                "decision": "merge",
                "target": "[[real-note]]",
                "new_content": (
                    "---\n"
                    "date: 2026-07-29\n"
                    # type and tags intentionally missing
                    "---\n"
                    "# Real Note\n\nHostile takeover.\n"
                ),
            }
        )

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )

    async def run() -> tuple[dict[str, object], Path | str | None]:
        return await summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": ["testing"],
                "session_id": "session-merge-attack",
            },
            "summary-model",
            False,
            summarize_sessions.anyio.Semaphore(1),
            ["testing"],
            False,
            vault,
            cluster_model=None,
        )

    entry, written = asyncio.run(run())

    # The merge was refused: written is None and a failure reason was recorded.
    assert written is None
    assert entry.get(summarize_sessions._FAILURE_REASON_KEY), (
        "merge refusal must mark_failure() so the attempts cap can dead-letter it"
    )
    # SEC-107 core invariant: the target note is byte-identical to the original.
    assert target.read_text(encoding="utf-8") == original
    # No backup directory was created (validation runs before backup).
    assert not (vault / ".trash").exists()


def test_summarize_one_merge_valid_content_backs_up_target_and_writes_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SEC-107: a VALID merge decision still triggers the backup-then-write
    sequence. The pre-existing note is preserved in .trash/backup/<today>/ and
    the target is overwritten with the new content."""
    summarize_sessions = _fresh_summarize_sessions(monkeypatch)
    transcript_path = tmp_path / "session.jsonl"
    transcript_path.write_text(
        '{"type":"user","content":"Investigate"}\n'
        '{"type":"assistant","content":"More"}\n',
        encoding="utf-8",
    )
    vault = tmp_path / "vault"
    debugging = vault / "Debugging"
    debugging.mkdir(parents=True)
    original = (
        "---\n"
        "date: 2026-06-01\n"
        "type: debugging\n"
        "tags: [debugging]\n"
        "---\n"
        "# Real Note\n\nOriginal insight.\n"
    )
    target = debugging / "real-note.md"
    target.write_text(original, encoding="utf-8")

    async def fake_preprocess(*args: object, **kwargs: object) -> str:
        return "cleaned transcript"

    new_content = (
        "---\n"
        "date: 2026-07-29\n"
        "type: debugging\n"
        "tags: [debugging]\n"
        'related: ["[[some-other]]"]\n'
        "---\n"
        "# Real Note\n\nUpdated insight.\n"
    )

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: object) -> str:
        return json.dumps(
            {
                "decision": "merge",
                "target": "[[real-note]]",
                "new_content": new_content,
            }
        )

    monkeypatch.setattr(
        _pipeline_module(), "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        _pipeline_module(), "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        _pipeline_module(), "_find_dedup_candidates", lambda *a, **k: []
    )
    # Force strip_unresolved_wikilinks to be a no-op so [[some-other]] stays
    # (the test exercises the merge write path, not link stripping).
    monkeypatch.setattr(
        summarize_sessions.vault_links,
        "strip_unresolved_wikilinks",
        lambda content, v: (content, 0),
    )

    async def run() -> tuple[dict[str, object], Path | str | None]:
        return await summarize_sessions.summarize_one(
            {
                "transcript_path": str(transcript_path),
                "project": "parsidion",
                "categories": ["testing"],
                "session_id": "session-merge-ok",
            },
            "summary-model",
            False,
            summarize_sessions.anyio.Semaphore(1),
            ["testing"],
            False,
            vault,
            cluster_model=None,
        )

    entry, written = asyncio.run(run())

    assert isinstance(written, Path)
    assert written.name == "real-note.md"
    # The target was overwritten with the new (valid) content.
    assert "Updated insight." in written.read_text(encoding="utf-8")
    # SEC-107: the original is preserved in the .trash backup directory.
    today = summarize_sessions.date.today().isoformat()
    backup = vault / ".trash" / "backup" / today / "Debugging" / "real-note.md"
    assert backup.is_file(), "merge backup must be created before the overwrite"
    assert backup.read_text(encoding="utf-8") == original
