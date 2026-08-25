"""Tests for parsight_backend.ensure_vault_indexed / spawn_background_index (Task 4)."""

from __future__ import annotations

import sys
from collections.abc import Generator
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core import parsight_backend  # noqa: E402 — ARC-006: patch internals where they live

from tests.fake_parsight import (  # noqa: E402
    FakeHealth,
    FakeMcpDaemon,
    FakeParsight,
)


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never write background-index logs into the real ~/.claude/logs."""
    monkeypatch.setattr(parsight_backend, "_LOG_DIR", tmp_path / "logs")


@pytest.fixture()
def ready(
    tmp_vault: Path, fake_parsight: FakeParsight, fake_parsight_health: FakeHealth
) -> FakeParsight:
    return fake_parsight


@pytest.fixture()
def mcp_daemon(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeMcpDaemon]:
    """Serve /health plus a minimal MCP endpoint; point PARSIGHT_MCP_URL at it.

    Unlike the plain ``fake_parsight_health`` fixture (health only — every
    POST fails, so the watch-coverage probe degrades to "unknown"), this
    daemon answers the probe, letting tests pin both the skip path and the
    spawn-anyway path.
    """
    daemon = FakeMcpDaemon().start()
    monkeypatch.setenv("PARSIGHT_MCP_URL", daemon.url)
    yield daemon
    daemon.stop()


def _repos_payload(vault: Path, *, stale: bool = False) -> dict[str, object]:
    """Verbatim list_indexed_repositories shape with one primary worktree."""
    return {
        "repositories": [
            {
                "repo_id": "r-vault",
                "root_path": str(vault),
                "file_count": 12,
                "symbol_count": 340,
                "last_indexed_at": 1700000000000,
                "current_branch": "main",
                "current_head": "def456" if stale else "abc123",
                "worktrees": [
                    {
                        "worktree_id": "wt-1",
                        "path": str(vault),
                        "branch": "main",
                        "is_primary": True,
                        "indexed_head": "abc123",
                        "current_head": "def456" if stale else "abc123",
                        "stale": stale,
                        "last_indexed_at": 1700000000000,
                        "file_count": 12,
                        "symbol_count": 340,
                    }
                ],
            }
        ],
        "_meta": {"count": 1},
    }


class TestEnsureVaultIndexed:
    def test_fresh_returns_true_without_spawn(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        ready.configure(repos=_repos_payload(tmp_vault))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        ready.wait_for_call("repos")
        ready.assert_no_call("index")

    def test_stale_spawns_background_index_and_returns_true(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # Stale is still usable: THIS query serves from the existing index
        # (True) while a background reindex catches it up. The plain health
        # fixture serves no MCP endpoint, so watch coverage is unknown and
        # the manual index still fires — with --no-wait so the detached CLI
        # submits the job and exits instead of polling it (orphaned-process
        # fix, cross-repo parsight card 019fe747).
        ready.configure(repos=_repos_payload(tmp_vault, stale=True))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        call = ready.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]

    def test_stale_skips_spawn_when_daemon_watches_vault(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
    ) -> None:
        # The daemon's own watcher covers the vault and re-indexes on file
        # change; a manual index job would only queue behind it (the writer
        # contention that produced the orphaned stuck processes).
        mcp_daemon.watched_paths = [str(tmp_vault)]
        fake_parsight.configure(repos=_repos_payload(tmp_vault, stale=True))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        fake_parsight.wait_for_call("repos")
        fake_parsight.assert_no_call("index")

    def test_stale_skips_spawn_via_symlinked_watch_path(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
    ) -> None:
        # Watch coverage compares canonicalized paths, so a differently
        # spelled (symlinked) watch entry still covers the vault.
        link = tmp_path / "vault-link"
        try:
            link.symlink_to(tmp_vault)
        except (OSError, NotImplementedError):
            pytest.skip("platform cannot create symlinks")
        mcp_daemon.watched_paths = [str(link)]
        fake_parsight.configure(repos=_repos_payload(tmp_vault, stale=True))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        fake_parsight.assert_no_call("index")

    def test_stale_spawns_when_watch_list_excludes_vault(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
    ) -> None:
        # Watch coverage is KNOWN and does not include the vault: the manual
        # index is the only catch-up mechanism, so it fires.
        mcp_daemon.watched_paths = [str(tmp_path / "other-vault")]
        fake_parsight.configure(repos=_repos_payload(tmp_vault, stale=True))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        call = fake_parsight.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]

    def test_missing_repo_spawns_background_index(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        mcp_daemon: FakeMcpDaemon,
    ) -> None:
        # Absent must still bootstrap even when watched: a watch only reacts
        # to file changes, it never performs a never-indexed repo's initial
        # index, so skipping here would leave the vault permanently unindexed.
        mcp_daemon.watched_paths = [str(tmp_vault)]
        fake_parsight.configure(repos={"repositories": [], "_meta": {"count": 0}})
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is False
        call = fake_parsight.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]

    def test_worktree_path_entry_counts(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # The vault may match a linked worktree's `path` rather than the
        # repo's root_path; that worktree's `stale` flag decides.
        ready.configure(
            repos={
                "repositories": [
                    {
                        "repo_id": "r-x",
                        "root_path": "/somewhere/else",
                        "file_count": 1,
                        "symbol_count": 1,
                        "last_indexed_at": None,
                        "current_branch": "main",
                        "current_head": None,
                        "worktrees": [
                            {
                                "worktree_id": "wt-2",
                                "path": str(tmp_vault),
                                "branch": "main",
                                "is_primary": False,
                                "indexed_head": "abc123",
                                "current_head": "abc123",
                                "stale": False,
                                "last_indexed_at": None,
                                "file_count": 1,
                                "symbol_count": 1,
                            }
                        ],
                    }
                ],
                "_meta": {"count": 1},
            }
        )
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        ready.assert_no_call("index")

    def test_repos_error_returns_false_without_spawn(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # `repos` is proxy-only: exit 2 = daemon-unreachable per the contract.
        ready.configure(exit_code=2)
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is False
        ready.assert_no_call("index")

    def test_repos_garbage_returns_false_without_spawn_and_logs(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        ready.configure(stdout_override="nope {")
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is False
        ready.assert_no_call("index")
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        assert "ParsightBackend" in log

    def test_unavailable_backend_returns_false_fast(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        # No health fixture: autouse isolation makes the probe fail.
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is False
        fake_parsight.assert_no_call("repos", settle=0.1)


class TestSpawnBackgroundIndex:
    def test_spawns_detached_with_json_and_logs(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        # --no-wait: the detached CLI submits the job and exits immediately
        # instead of blocking on job polling (nobody reads the NDJSON log or
        # the Popen return value, so waiting could only ever orphan a process).
        assert parsight_backend.spawn_background_index(tmp_vault) is True
        call = ready.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json", "--no-wait"]
        assert (parsight_backend._LOG_DIR / parsight_backend._LOG_NAME).exists()

    def test_returns_false_when_unavailable(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        assert parsight_backend.spawn_background_index(tmp_vault) is False
        fake_parsight.assert_no_call("index", settle=0.1)


class TestDaemonWatchesVault:
    """Unit tests for the ``_daemon_watches_vault`` MCP probe itself.

    The parsight CLI has no watch-list subcommand, so the backend asks the
    daemon's MCP endpoint directly (initialize → tools/call
    list_watched_paths). The fake daemon mirrors the real one's handshake
    discipline — a session-less tools/call gets 422 — so these tests also
    prove the probe performs the initialize handshake.
    """

    def test_true_when_path_listed_and_sends_session_header(
        self, tmp_vault: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        mcp_daemon.watched_paths = [str(tmp_vault)]
        assert parsight_backend._daemon_watches_vault(tmp_vault) is True
        methods = [c.get("method") for c in mcp_daemon.mcp_calls]
        assert methods[0] == "initialize"
        assert "tools/call" in methods
        call_entry = next(
            c for c in mcp_daemon.mcp_calls if c.get("method") == "tools/call"
        )
        assert call_entry.get("session")  # handshake honored, not session-less

    def test_true_via_symlinked_watch_path(
        self, tmp_vault: Path, tmp_path: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        link = tmp_path / "vault-link"
        try:
            link.symlink_to(tmp_vault)
        except (OSError, NotImplementedError):
            pytest.skip("platform cannot create symlinks")
        mcp_daemon.watched_paths = [str(link)]
        assert parsight_backend._daemon_watches_vault(tmp_vault) is True

    def test_false_when_list_empty(
        self, tmp_vault: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False

    def test_false_on_jsonrpc_error(
        self, tmp_vault: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        mcp_daemon.raw_tools_response = (
            'data: {"jsonrpc":"2.0","id":2,"error":{"code":-32601,'
            '"message":"tool unavailable"}}\n\n'
        )
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False

    def test_false_on_garbage_sse_body(
        self, tmp_vault: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        # Mirrors the real daemon's SSE framing: keep-alive/noise lines around
        # the payload. Here every data line is unparseable.
        mcp_daemon.raw_tools_response = "data: \nretry: 3000\n\ndata: not json\n\n"
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False

    def test_false_when_mcp_endpoint_not_served(
        self, tmp_vault: Path, mcp_daemon: FakeMcpDaemon
    ) -> None:
        mcp_daemon.serve_mcp = False
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False

    def test_false_when_post_unsupported(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        # Plain health-only fixture: POST gets 501 from http.server — the
        # degrade path when a daemon (or proxy) lacks the MCP endpoint.
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False

    def test_never_raises_on_transport_explosion(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("transport exploded")

        monkeypatch.setattr(parsight_backend.urllib.request, "urlopen", boom)
        assert parsight_backend._daemon_watches_vault(tmp_vault) is False
