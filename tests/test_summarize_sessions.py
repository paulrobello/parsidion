from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

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
