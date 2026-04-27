"""Unit tests for session_start_hook.py safety guards."""

from __future__ import annotations

import importlib
import subprocess
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

        def _fail_popen(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("Popen should not run when the AI lock is busy")

        monkeypatch.setattr(session_start_hook.subprocess, "Popen", _fail_popen)

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

    def test_kills_process_group_on_ai_timeout(
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

        candidate = tmp_path / "note.md"
        candidate.write_text("ignored", encoding="utf-8")

        class _FakeProc:
            pid = 4321
            returncode = None

            def communicate(self, timeout: int) -> tuple[str, str]:
                raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)

            def wait(self) -> int:
                return 0

            def kill(self) -> None:
                raise AssertionError("kill() should not be used when killpg succeeds")

        monkeypatch.setattr(
            session_start_hook.subprocess,
            "Popen",
            lambda *args, **kwargs: _FakeProc(),
        )
        monkeypatch.setattr(session_start_hook.os, "getpgid", lambda pid: pid)

        killed: list[tuple[int, int]] = []
        monkeypatch.setattr(
            session_start_hook.os,
            "killpg",
            lambda pgid, sig: killed.append((pgid, sig)),
        )

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[candidate],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert killed == [(4321, session_start_hook.signal.SIGKILL)]
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

        def _fail_popen(*args: object, **kwargs: object) -> None:
            nonlocal called
            called = True
            raise AssertionError("Popen should not run while cooldown is active")

        monkeypatch.setattr(session_start_hook.subprocess, "Popen", _fail_popen)

        result = session_start_hook._select_context_with_ai(
            project_name="parsidion",
            cwd=str(tmp_path),
            candidate_notes=[tmp_path / "note.md"],
            model="claude-haiku-test",
            vault_path=tmp_path,
        )

        assert result == ""
        assert called is False

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

        class _FakeProc:
            pid = 4321
            returncode = 0

            def communicate(self, timeout: int) -> tuple[str, str]:
                return ("### Note Title (path/to/note.md)\nKey point 1", "")

        monkeypatch.setattr(
            session_start_hook.subprocess,
            "Popen",
            lambda *args, **kwargs: _FakeProc(),
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
