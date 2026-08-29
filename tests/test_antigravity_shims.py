"""Behavioral tests for Antigravity PreInvocation and Stop shims."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import agent_adapter  # noqa: E402
import antigravity_session_end_hook as end_shim  # noqa: E402
import antigravity_session_start_hook as start_shim  # noqa: E402


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, list[Any]]:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = SimpleNamespace(
        session_start_hook=SimpleNamespace(max_chars=4000),
        session_stop_hook=SimpleNamespace(
            transcript_tail_lines=200,
            transcript_tail_bytes=100_000,
            pi_transcript_tail_lines=1000,
            ai_timeout=1,
            ai_model=None,
            auto_summarize=False,
            auto_summarize_after=999,
        ),
        transcripts=SimpleNamespace(
            tail_lines=200, tail_bytes=100_000, max_line_bytes=50_000
        ),
        adaptive_context=SimpleNamespace(enabled=False),
    )
    calls: dict[str, list[Any]] = {"pending": [], "daily": [], "context": []}
    monkeypatch.setattr(agent_adapter, "resolve_vault", lambda **_: vault)
    monkeypatch.setattr(agent_adapter, "load_typed_config", lambda **_: cfg)
    monkeypatch.setattr(agent_adapter, "load_config", lambda **_: {})
    monkeypatch.setattr(agent_adapter, "ensure_vault_dirs", lambda **_: None)
    monkeypatch.setattr(
        agent_adapter,
        "append_to_pending",
        lambda *a, **kw: calls["pending"].append((a, kw)),
    )
    monkeypatch.setattr(
        agent_adapter,
        "append_session_to_daily",
        lambda *a, **kw: calls["daily"].append((a, kw)),
    )
    monkeypatch.setattr(agent_adapter, "git_commit_vault", lambda *a, **kw: None)
    monkeypatch.setattr(agent_adapter, "_launch_summarizer_if_pending", lambda *_: None)
    monkeypatch.setattr(agent_adapter, "_update_adaptive_scores", lambda *a, **kw: None)
    monkeypatch.setattr(agent_adapter, "write_hook_event", lambda *a, **kw: None)
    monkeypatch.setattr(agent_adapter, "get_project_name", lambda _: "test-project")
    monkeypatch.setattr(
        agent_adapter,
        "_classify_session",
        lambda *a, **kw: (
            {"debugging": ["Root cause fixed"]},
            "summary",
            True,
            True,
            "keyword",
        ),
    )
    import session_start_hook

    monkeypatch.setattr(
        session_start_hook,
        "build_session_context",
        lambda cwd, **kw: calls["context"].append(cwd) or ("CONTEXT", 1),
    )
    return calls


def _stdin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(value))


@pytest.mark.parametrize("value", [None, False, "0", 3])
def test_start_off_contract_invocation_is_quiet(
    value: object,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    payload = {} if value is None else {"invocationNum": value}
    _stdin(monkeypatch, json.dumps(payload))
    start_shim.main()
    assert capsys.readouterr().out == "{}"
    assert calls["context"] == []


def test_start_malformed_stdin_is_quiet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    _stdin(monkeypatch, "not-json")
    start_shim.main()
    assert capsys.readouterr().out == "{}"
    assert calls["context"] == []


def test_start_zero_injects_ephemeral_message_and_maps_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"
    _stdin(
        monkeypatch,
        json.dumps(
            {
                "invocationNum": 0,
                "workspacePaths": [str(workspace), "/ignored"],
                "conversationId": "conv-42",
                "transcriptPath": "/tmp/transcript.jsonl",
            }
        ),
    )
    start_shim.main()
    output = json.loads(capsys.readouterr().out)
    assert output == {"injectSteps": [{"ephemeralMessage": "CONTEXT"}]}
    assert calls["context"] == [str(workspace)]


def test_camel_case_payload_mapping() -> None:
    assert start_shim._to_shared_payload(
        {
            "workspacePaths": ["/first", "/second"],
            "conversationId": "conv",
            "transcriptPath": "/t.jsonl",
        }
    ) == {"cwd": "/first", "session_id": "conv", "transcript_path": "/t.jsonl"}


def _transcript(
    tmp_path: Path,
    conversation_id: str = "conv-42",
    leaf_dir: str = ".system_generated",
) -> tuple[Path, Path, Path]:
    """Create a transcript under a temp Antigravity home; returns
    (project, transcript, home)."""
    project = tmp_path / "project"
    home = tmp_path / "antigravity-home"
    path = (
        home
        / "antigravity-cli"
        / "brain"
        / conversation_id
        / leaf_dir
        / "logs"
        / "transcript.jsonl"
    )
    path.parent.mkdir(parents=True)
    path.write_text('{"role":"model","content":"Root cause fixed"}\n', encoding="utf-8")
    return project, path, home


def test_stop_not_fully_idle_allows_without_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    project, transcript, home = _transcript(tmp_path)
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    _stdin(
        monkeypatch,
        json.dumps(
            {
                "fullyIdle": False,
                "workspacePaths": [str(project)],
                "transcriptPath": str(transcript),
            }
        ),
    )
    end_shim.main()
    assert json.loads(capsys.readouterr().out) == {"decision": ""}
    assert calls["pending"] == []
    assert calls["daily"] == []


def test_stop_malformed_stdin_is_safe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    _stdin(monkeypatch, "not-json")
    end_shim.main()
    assert json.loads(capsys.readouterr().out) == {"decision": ""}
    assert calls["pending"] == []


def test_stop_queues_conversation_id_not_transcript_stem(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    project, transcript, home = _transcript(tmp_path, "conversation-abc")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    _stdin(
        monkeypatch,
        json.dumps(
            {
                "fullyIdle": True,
                "workspacePaths": [str(project)],
                "conversationId": "conversation-abc",
                "transcriptPath": str(transcript),
            }
        ),
    )
    end_shim.main()
    assert json.loads(capsys.readouterr().out) == {"decision": ""}
    assert len(calls["pending"]) == 1
    args, kwargs = calls["pending"][0]
    assert args[0] == transcript
    assert kwargs["session_id"] == "conversation-abc"
    assert kwargs["session_id"] != transcript.stem
    assert calls["daily"]


def test_stop_missing_transcript_allows_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(tmp_path / "antigravity-home"))
    _stdin(
        monkeypatch,
        json.dumps(
            {
                "fullyIdle": True,
                "workspacePaths": [str(tmp_path)],
                "transcriptPath": "/missing/transcript.jsonl",
            }
        ),
    )
    end_shim.main()
    assert json.loads(capsys.readouterr().out) == {"decision": ""}
    assert calls["pending"] == []


def test_stop_no_assistant_texts_allows_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _patch_pipeline(monkeypatch, tmp_path)
    project, transcript, home = _transcript(tmp_path, "silent-conv")
    transcript.write_text('{"role":"user","content":"question"}\n', encoding="utf-8")
    monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
    _stdin(
        monkeypatch,
        json.dumps(
            {
                "fullyIdle": True,
                "workspacePaths": [str(project)],
                "conversationId": "silent-conv",
                "transcriptPath": str(transcript),
            }
        ),
    )
    end_shim.main()
    assert json.loads(capsys.readouterr().out) == {"decision": ""}
    assert calls["pending"] == []


class TestAntigravityTranscriptPathValidator:
    def test_accepts_documented_cli_layout(self, monkeypatch, tmp_path) -> None:
        from vault_hooks import is_antigravity_transcript_path

        home = tmp_path / "antigravity-home"
        monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
        good = (
            home
            / "antigravity-cli"
            / "brain"
            / "conv-1"
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        assert is_antigravity_transcript_path(good) is True

    def test_rejects_lookalike_system_generated(self, monkeypatch, tmp_path) -> None:
        from vault_hooks import is_antigravity_transcript_path

        home = tmp_path / "antigravity-home"
        monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
        lookalike = (
            home
            / "antigravity-cli"
            / "brain"
            / "conv-1"
            / "xsystem_generated"
            / "logs"
            / "transcript.jsonl"
        )
        assert is_antigravity_transcript_path(lookalike) is False

    def test_rejects_project_local_gemini_tree(self, monkeypatch, tmp_path) -> None:
        from vault_hooks import is_antigravity_transcript_path

        home = tmp_path / "antigravity-home"
        monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
        project = tmp_path / "project"
        local = (
            project
            / ".gemini"
            / "antigravity-cli"
            / "brain"
            / "conv-1"
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )
        assert is_antigravity_transcript_path(local, cwd=str(project)) is False

    def test_rejects_config_files_in_home(self, monkeypatch, tmp_path) -> None:
        from vault_hooks import is_antigravity_transcript_path

        home = tmp_path / "antigravity-home"
        monkeypatch.setenv("ANTIGRAVITY_HOME", str(home))
        assert is_antigravity_transcript_path(home / "settings.json") is False
        assert (
            is_antigravity_transcript_path(home / "config" / "mcp_config.json") is False
        )
        assert (
            is_antigravity_transcript_path(
                home / "antigravity-ide" / "somewhere" / "transcript.jsonl"
            )
            is False
        )
