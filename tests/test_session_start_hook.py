"""Unit tests for session_start_hook.py safety guards."""

from __future__ import annotations

import importlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
session_start_hook = importlib.import_module("session_start_hook")


def _write_codex_config(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "config.yaml").write_text(
        "ai:\n"
        "  backend: codex-cli\n"
        "session_start_hook:\n"
        "  ai_model: null\n"
        "  ai_single_flight: false\n"
        "  ai_cooldown_seconds: 0\n"
        "  ai_timeout: 5\n"
        "  track_delta: false\n"
        "  use_embeddings: false\n",
        encoding="utf-8",
    )


def _run_session_start_main_for_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
) -> list[list[str]]:
    vault = tmp_path / "vault"
    project = tmp_path / "project"
    note = vault / "Projects" / "codex-note.md"
    project.mkdir(parents=True)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Codex Note\nUse Codex backend defaults.\n", encoding="utf-8")
    _write_codex_config(vault)

    session_start_hook.vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    session_start_hook.vault_common._clear_config_cache()
    monkeypatch.setenv("CLAUDE_VAULT", str(vault))
    monkeypatch.setattr(session_start_hook, "_build_candidates", lambda *_args: [note])
    monkeypatch.setattr(
        session_start_hook.vault_common, "write_hook_event", lambda **_kwargs: None
    )

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        output_path = Path(cmd[cmd.index("--output-last-message") + 1])
        output_path.write_text(
            "### Codex Note\nUse Codex backend defaults.", encoding="utf-8"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(
        session_start_hook.ai_backend, "_run_prompt_subprocess", fake_run
    )
    monkeypatch.setattr(sys, "argv", ["session_start_hook.py", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(project)})))
    monkeypatch.setattr(sys, "stdout", io.StringIO())

    session_start_hook.main()
    assert calls
    return calls


class TestAiSelectionSafety:
    """Tests for SessionStart AI safety guards."""

    def test_skips_ai_when_single_flight_lock_is_busy(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                True
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_try_acquire_ai_lock",
            lambda vault_path: None,
        )

        called = False

        def _fail_run_ai_prompt(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("AI backend should not run when the AI lock is busy")

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", _fail_run_ai_prompt
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

    def test_releases_lock_when_ai_backend_returns_no_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                True
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 1
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_try_acquire_ai_lock",
            lambda vault_path: object(),
        )

        released = False

        def _release(lock_file: object | None) -> None:
            nonlocal released
            released = True

        monkeypatch.setattr(session_start_hook, "_release_ai_lock", _release)
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "read_note_summary",
            lambda path, max_lines=6: "Useful summary",
        )
        calls: list[dict[str, object]] = []

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> None:
            calls.append({"prompt": prompt, **kwargs})
            return None

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        candidate = tmp_path / "note.md"
        candidate.write_text("ignored", encoding="utf-8")

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[candidate],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert calls[0]["timeout"] == 1
        assert released is True

    def test_skips_ai_when_cooldown_is_active(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_is_ai_cooldown_active",
            lambda vault_path: True,
        )

        called = False

        def _fail_run_ai_prompt(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("AI backend should not run while cooldown is active")

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", _fail_run_ai_prompt
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[tmp_path / "note.md"],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

    def test_select_context_with_ai_uses_small_tier_backend_and_writes_cooldown_stamp(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 7
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        note = tmp_path / "Patterns" / "codex-exec.md"
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\ntags: [codex]\n---\n# Codex Exec\nUse codex exec for non-interactive prompts.\n",
            encoding="utf-8",
        )
        calls: list[dict[str, object]] = []

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
            calls.append({"prompt": prompt, **kwargs})
            return "### Codex Exec\nUse codex exec for non-interactive prompts."

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        context = session_start_hook._select_context_with_ai(
            "parsidion",
            str(tmp_path),
            [note],
            None,
            4000,
            vault_path=tmp_path,
        )

        assert "Codex Exec" in context
        assert calls
        assert calls[0]["model"] is None
        assert calls[0]["model_tier"] == "small"
        assert calls[0]["timeout"] == 7
        assert calls[0]["purpose"] == "session-start-selection"
        assert calls[0]["cwd"] == str(tmp_path)
        assert calls[0]["vault"] == tmp_path
        assert (tmp_path / session_start_hook._AI_STAMP_FILENAME).exists()

    def test_main_no_arg_ai_uses_codex_backend_default_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls = _run_session_start_main_for_codex(monkeypatch, tmp_path, ["--ai"])

        cmd = calls[0]
        assert cmd[cmd.index("--model") + 1] == "gpt-5.5"
        assert "claude-haiku-4-5-20251001" not in cmd

    def test_main_explicit_ai_model_overrides_codex_backend_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        calls = _run_session_start_main_for_codex(
            monkeypatch, tmp_path, ["--ai", "custom-codex-model"]
        )

        cmd = calls[0]
        assert cmd[cmd.index("--model") + 1] == "custom-codex-model"

    def test_writes_cooldown_stamp_after_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "get_config",
            lambda section, key, default=None: (
                False
                if (section, key) == ("session_start_hook", "ai_single_flight")
                else 30
                if (section, key) == ("session_start_hook", "ai_cooldown_seconds")
                else 1
                if (section, key) == ("session_start_hook", "ai_timeout")
                else default
            ),
        )
        monkeypatch.setattr(
            session_start_hook,
            "_is_ai_cooldown_active",
            lambda vault_path: False,
        )
        monkeypatch.setattr(
            session_start_hook.vault_common,
            "read_note_summary",
            lambda path, max_lines=6: "Useful summary",
        )

        candidate = tmp_path / "note.md"
        candidate.write_text("ignored", encoding="utf-8")

        def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
            return "### Note Title (path/to/note.md)\nKey point 1"

        monkeypatch.setattr(
            session_start_hook.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )

        stamped: list[Path] = []
        monkeypatch.setattr(
            session_start_hook,
            "_write_ai_cooldown_stamp",
            lambda vault_path: stamped.append(vault_path),
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[candidate],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == "### Note Title (path/to/note.md)\nKey point 1"
        assert stamped == [tmp_path]
