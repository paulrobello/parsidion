"""Tests for vault_search's par-mem backend selector (Task 5)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import parmem_backend  # noqa: E402
import vault_common  # noqa: E402

from tests.fake_parmem import FakeHealth, FakeParMem  # noqa: E402

vault_search = importlib.import_module("vault_search")

SENTINEL: list[dict[str, object]] = [{"stem": "from-embeddings"}]


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.load_config.cache_clear()
    parmem_backend.reset_parmem_cache()


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


@pytest.fixture()
def embeddings_sentinel(monkeypatch: pytest.MonkeyPatch) -> list[list[object]]:
    """Replace the embeddings leg with a sentinel; records call args."""
    calls: list[list[object]] = []

    def fake(
        query: str,
        top: int = 10,
        min_score: float = 0.45,
        model_name: str = "",
        vault: Path | None = None,
    ) -> list[dict[str, object]]:
        calls.append([query, top, min_score, model_name, vault])
        return SENTINEL

    monkeypatch.setattr(vault_search, "_search_embeddings", fake)
    return calls


@pytest.fixture()
def ready(
    tmp_vault: Path, fake_parmem: FakeParMem, fake_parmem_health: FakeHealth
) -> FakeParMem:
    fake_parmem.configure(
        repos=_repos_payload(tmp_vault),
        find_code={"results": [{"file_path": "Patterns/hit-note.md", "score": 0.05}]},
    )
    note = tmp_vault / "Patterns" / "hit-note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("# Hit Note\nBody.\n", encoding="utf-8")
    return fake_parmem


class TestBackendSelection:
    def test_none_backend_returns_empty_without_embeddings(
        self, tmp_vault: Path, embeddings_sentinel: list[list[object]]
    ) -> None:
        assert vault_search.search("q", backend="none") == []
        assert embeddings_sentinel == []
        assert vault_search.LAST_BACKEND == "none"

    def test_embeddings_backend_ignores_parmem(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        assert vault_search.search("q", backend="embeddings") == SENTINEL
        ready.assert_no_call("find-code", settle=0.1)
        assert vault_search.LAST_BACKEND == "embeddings"

    def test_auto_serves_from_parmem_when_available(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        results = vault_search.search("q", vault=tmp_vault)  # backend from config: auto
        assert [r["stem"] for r in results] == ["hit-note"]
        assert embeddings_sentinel == []
        assert vault_search.LAST_BACKEND == "par-mem"

    def test_auto_falls_back_when_backend_unavailable(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert vault_search.search("q", top=7, min_score=0.3) == SENTINEL
        # Byte-identical delegation: embeddings leg got the exact args.
        assert embeddings_sentinel[0][:3] == ["q", 7, 0.3]
        assert vault_search.LAST_BACKEND == "embeddings"

    def test_auto_unindexed_falls_back_to_embeddings(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        ready.configure(repos={"repositories": [], "_meta": {"count": 0}})
        assert vault_search.search("q", vault=tmp_vault) == SENTINEL
        ready.wait_for_call("index")  # background index kicked
        assert vault_search.LAST_BACKEND == "embeddings"

    def test_auto_stale_serves_parmem_and_kicks_reindex(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        # Stale-but-usable: this query is served from par-mem's existing
        # index while a background reindex catches it up.
        ready.configure(
            repos=_repos_payload(tmp_vault, stale=True),
            find_code={
                "results": [{"file_path": "Patterns/hit-note.md", "score": 0.05}]
            },
        )
        results = vault_search.search("q", vault=tmp_vault)
        assert [r["stem"] for r in results] == ["hit-note"]
        ready.wait_for_call("index")  # background reindex kicked
        assert embeddings_sentinel == []
        assert vault_search.LAST_BACKEND == "par-mem"

    def test_auto_find_code_failure_falls_back(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        ready.configure(repos=_repos_payload(tmp_vault), exit_codes={"find-code": 1})
        assert vault_search.search("q", vault=tmp_vault) == SENTINEL
        assert vault_search.LAST_BACKEND == "embeddings"

    def test_explicit_parmem_has_no_embeddings_fallback(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert vault_search.search("q", backend="par-mem") == []
        assert embeddings_sentinel == []

    def test_config_backend_read_when_arg_absent(
        self, tmp_vault: Path, embeddings_sentinel: list[list[object]]
    ) -> None:
        _write_config(tmp_vault, "search:\n  backend: none\n")
        assert vault_search.search("q") == []
        assert embeddings_sentinel == []

    def test_invalid_config_backend_treated_as_auto(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        embeddings_sentinel: list[list[object]],
    ) -> None:
        _write_config(tmp_vault, "search:\n  backend: warp-drive\n")
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert vault_search.search("q") == SENTINEL

    def test_both_backends_unavailable_returns_empty_and_metadata_works(
        self, tmp_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        # No embeddings.db, no par-mem: today's behavior — [] semantic.
        assert vault_search.search("q", vault=tmp_vault) == []
        # Metadata mode is untouched by backend selection.
        assert vault_search.query(tag="python", vault=tmp_vault) == []


class TestCli:
    def test_backend_flag_bypasses_embeddings_db_check(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "vault-search",
                "q",
                "--backend",
                "par-mem",
                "--json",
                "-V",
                str(tmp_vault),
            ],
        )
        vault_search.main()
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert [r["stem"] for r in parsed] == ["hit-note"]

    def test_missing_db_message_preserved_when_parmem_absent(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        monkeypatch.setattr(
            sys, "argv", ["vault-search", "q", "--json", "-V", str(tmp_vault)]
        )
        with pytest.raises(SystemExit) as exc:
            vault_search.main()
        assert exc.value.code == 0
        assert "embeddings.db not found" in capsys.readouterr().err

    def test_rich_output_names_serving_backend(
        self,
        tmp_vault: Path,
        ready: FakeParMem,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "vault-search",
                "q",
                "--backend",
                "par-mem",
                "--rich",
                "-V",
                str(tmp_vault),
            ],
        )
        vault_search.main()
        assert "backend: par-mem" in capsys.readouterr().err


class TestInteractiveBackend:
    def test_interactive_flag_threads_backend_through(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--backend/-B must reach vault_tui.interactive_search, not be dropped."""
        import vault_tui

        calls: list[dict[str, object]] = []

        def fake_interactive_search(
            vault: Path | None = None, backend: str | None = None
        ) -> None:
            calls.append({"vault": vault, "backend": backend})

        monkeypatch.setattr(vault_tui, "interactive_search", fake_interactive_search)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "vault-search",
                "-i",
                "-B",
                "embeddings",
                "-V",
                str(tmp_vault),
            ],
        )
        vault_search.main()
        assert len(calls) == 1
        assert calls[0]["backend"] == "embeddings"
