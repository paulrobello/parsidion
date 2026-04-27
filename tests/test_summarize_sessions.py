from __future__ import annotations

import asyncio
import importlib
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
    return importlib.import_module("summarize_sessions")


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
        summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt
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
        summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt
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
        transcript_path_str: str, tail_lines: int, max_chars: int | None
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
        summarize_sessions, "preprocess_transcript", fake_preprocess_transcript
    )
    monkeypatch.setattr(summarize_sessions, "_summarize_chunk", fake_summarize_chunk)

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

    monkeypatch.setattr(summarize_sessions, "_summarize_chunk", fake_summarize_chunk)

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
        summarize_sessions, "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(summarize_sessions.vault_common, "get_config", fake_get_config)
    monkeypatch.setattr(
        summarize_sessions, "_find_dedup_candidates", lambda *a, **k: []
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
        summarize_sessions, "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        summarize_sessions, "_find_dedup_candidates", lambda *a, **k: []
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
        summarize_sessions, "preprocess_transcript_hierarchical", fake_preprocess
    )
    monkeypatch.setattr(
        summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt
    )
    monkeypatch.setattr(
        summarize_sessions, "_find_dedup_candidates", lambda *a, **k: []
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
