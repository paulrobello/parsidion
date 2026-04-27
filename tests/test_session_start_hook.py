"""Unit tests for session_start_hook.py safety guards."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
session_start_hook = importlib.import_module("session_start_hook")


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
