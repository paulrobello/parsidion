"""Tests for parsight freshness triggers: update_index spawn + watch holds (Task 6)."""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core import parsight_backend  # noqa: E402 — ARC-006: patch internals where they live
import vault_common  # noqa: E402

from tests.fake_parsight import (  # noqa: E402
    FakeHealth,
    FakeMcpDaemon,
    FakeParsight,
)


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(parsight_backend, "_LOG_DIR", tmp_path / "logs")


@pytest.fixture()
def ready(
    tmp_vault: Path, fake_parsight: FakeParsight, fake_parsight_health: FakeHealth
) -> FakeParsight:
    return fake_parsight


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.load_config.cache_clear()
    parsight_backend.reset_parsight_cache()


class TestSpawnWatchHelpers:
    def test_spawn_watch_args(self, tmp_vault: Path, ready: FakeParsight) -> None:
        assert parsight_backend.spawn_watch(tmp_vault, "sess-123") is True
        call = ready.wait_for_call("watch")
        assert call["argv"] == [
            "watch",
            str(tmp_vault),
            "--hold-token",
            "parsidion-sess-123",
        ]

    def test_spawn_unwatch_args(self, tmp_vault: Path, ready: FakeParsight) -> None:
        assert parsight_backend.spawn_unwatch(tmp_vault, "sess-123") is True
        call = ready.wait_for_call("unwatch")
        assert call["argv"] == [
            "unwatch",
            str(tmp_vault),
            "--hold-token",
            "parsidion-sess-123",
        ]

    def test_empty_session_id_is_noop(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        assert parsight_backend.spawn_watch(tmp_vault, "  ") is False
        ready.assert_no_call("watch", settle=0.1)

    def test_unavailable_backend_is_noop(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        assert parsight_backend.spawn_watch(tmp_vault, "sess-123") is False
        fake_parsight.assert_no_call("watch", settle=0.1)


class TestUpdateIndexTrigger:
    def test_end_of_run_spawns_background_index(
        self,
        tmp_vault: Path,
        ready: FakeParsight,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import update_index

        # Disable embeddings so the run neither writes note_index nor spawns
        # the (heavy, uv-based) build_embeddings.py; the parsight trigger must
        # be independent of embeddings.enabled.
        _write_config(tmp_vault, "embeddings:\n  enabled: false\n")
        monkeypatch.setattr(sys, "argv", ["update_index.py", "--vault", str(tmp_vault)])
        update_index.main()
        call = ready.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]
        assert "parsight: background index launched" in capsys.readouterr().out

    def test_end_of_run_skips_spawn_when_daemon_watches_vault(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import update_index

        # The daemon answers the watch-coverage probe with this vault listed:
        # the watcher will index the very note writes this run produced, so
        # the manual end-of-run spawn must be skipped.
        mcp_daemon.watched_paths = [str(tmp_vault)]
        _write_config(tmp_vault, "embeddings:\n  enabled: false\n")
        monkeypatch.setattr(sys, "argv", ["update_index.py", "--vault", str(tmp_vault)])
        update_index.main()
        fake_parsight.assert_no_call("index", settle=0.1)
        assert "skipping background index" in capsys.readouterr().out

    def test_end_of_run_spawns_when_watch_list_excludes_vault(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import update_index

        mcp_daemon.watched_paths = [str(tmp_path / "other-vault")]
        _write_config(tmp_vault, "embeddings:\n  enabled: false\n")
        monkeypatch.setattr(sys, "argv", ["update_index.py", "--vault", str(tmp_vault)])
        update_index.main()
        call = fake_parsight.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]
        assert "parsight: background index launched" in capsys.readouterr().out

    def test_no_spawn_when_backend_unavailable(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
    ) -> None:
        # No fake_parsight_health fixture: the autouse isolation pins
        # PARSIGHT_MCP_URL at an unreachable port, so the health probe fails
        # and the backend is unavailable. PATH is left intact — update_index
        # shells out to git and must keep working exactly as today.
        import update_index

        _write_config(tmp_vault, "embeddings:\n  enabled: false\n")
        monkeypatch.setattr(sys, "argv", ["update_index.py", "--vault", str(tmp_vault)])
        update_index.main()  # must complete exactly as today
        fake_parsight.assert_no_call("index", settle=0.1)


class TestSessionStartWatch:
    def _run_hook(
        self, monkeypatch: pytest.MonkeyPatch, payload: dict[str, object]
    ) -> None:
        import session_start_hook

        monkeypatch.setattr(sys, "argv", ["session_start_hook.py"])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        session_start_hook.main()

    def test_holds_watch_with_session_token(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        ready: FakeParsight,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        self._run_hook(monkeypatch, {"cwd": str(proj), "session_id": "abc-1"})
        out = json.loads(capsys.readouterr().out)
        assert "hookSpecificOutput" in out  # hook output intact
        call = ready.wait_for_call("watch")
        assert call["argv"] == [
            "watch",
            str(tmp_vault),
            "--hold-token",
            "parsidion-abc-1",
        ]

    def test_no_session_id_no_watch(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        ready: FakeParsight,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        proj = tmp_path / "proj"
        proj.mkdir()
        self._run_hook(monkeypatch, {"cwd": str(proj)})
        assert "hookSpecificOutput" in json.loads(capsys.readouterr().out)
        ready.assert_no_call("watch", settle=0.1)


class TestSessionEndUnwatch:
    def test_releases_watch_before_transcript_early_return(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        ready: FakeParsight,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import session_stop_hook

        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.delenv("CLAUDE_VAULT_STOP_ACTIVE", raising=False)
        monkeypatch.delenv("PARSIDION_INTERNAL", raising=False)
        monkeypatch.setattr(sys, "argv", ["session_stop_hook.py"])
        # No transcript_path: the hook early-returns AFTER releasing the hold.
        payload = {"cwd": str(proj), "session_id": "abc-2"}
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
        try:
            session_stop_hook.main()
        finally:
            os.environ.pop("CLAUDE_VAULT_STOP_ACTIVE", None)
        assert capsys.readouterr().out == "{}"
        call = ready.wait_for_call("unwatch")
        assert call["argv"] == [
            "unwatch",
            str(tmp_vault),
            "--hold-token",
            "parsidion-abc-2",
        ]
