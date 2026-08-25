"""Tests for parsight_backend.ensure_vault_indexed / spawn_background_index (Task 4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core import parsight_backend  # noqa: E402 — ARC-006: patch internals where they live

from tests.fake_parsight import FakeHealth, FakeParsight  # noqa: E402


@pytest.fixture(autouse=True)
def _log_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never write background-index logs into the real ~/.claude/logs."""
    monkeypatch.setattr(parsight_backend, "_LOG_DIR", tmp_path / "logs")


@pytest.fixture()
def ready(
    tmp_vault: Path, fake_parsight: FakeParsight, fake_parsight_health: FakeHealth
) -> FakeParsight:
    return fake_parsight


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
        # (True) while a background reindex catches it up.
        ready.configure(repos=_repos_payload(tmp_vault, stale=True))
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is True
        call = ready.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json"]

    def test_missing_repo_spawns_background_index(
        self, tmp_vault: Path, ready: FakeParsight
    ) -> None:
        ready.configure(repos={"repositories": [], "_meta": {"count": 0}})
        assert parsight_backend.ensure_vault_indexed(tmp_vault) is False
        ready.wait_for_call("index")

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
        assert parsight_backend.spawn_background_index(tmp_vault) is True
        call = ready.wait_for_call("index")
        assert call["argv"] == ["index", str(tmp_vault), "--json"]
        assert (parsight_backend._LOG_DIR / parsight_backend._LOG_NAME).exists()

    def test_returns_false_when_unavailable(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        assert parsight_backend.spawn_background_index(tmp_vault) is False
        fake_parsight.assert_no_call("index", settle=0.1)
