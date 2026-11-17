"""get_vault_root must resolve ``--vault`` through the shared vault resolver.

Kanban 01a1e7a2: build_graph.py's ``get_vault_root`` treated ``--vault``
strictly as a filesystem path, so ``build_graph.py --vault NAME`` ignored
``~/.config/parsidion/vaults.yaml`` registered names. build_embeddings.py
already routes through ``resolve_vault(explicit=...)`` — these tests pin
the same contract for build_graph.py.

Independence: ``pytest.importorskip("numpy")`` keeps the core suite
numpy-free — build_graph.py is a ``uv run --no-project`` script with its
own inline numpy dependency, not a project dependency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import build_graph  # noqa: E402
from parsidion.core.vault_path import VaultConfigError  # noqa: E402


def _ns(vault: Path | None) -> argparse.Namespace:
    return argparse.Namespace(vault=vault)


class TestExplicitVaultArg:
    """The ``args.vault is not None`` branch of get_vault_root."""

    def test_registered_name_resolves_through_vaults_yaml(
        self, tmp_vault: Path
    ) -> None:
        assert build_graph.get_vault_root(_ns(vault=Path("test"))) == tmp_vault

    def test_filesystem_path_passes_through_resolved(self, tmp_vault: Path) -> None:
        # update_index → build_graph spawns pass an already-resolved path;
        # PATH input keeps resolving to itself.
        assert build_graph.get_vault_root(_ns(vault=tmp_vault)) == tmp_vault

    def test_unregistered_path_is_rejected(self, tmp_vault: Path) -> None:
        # SEC-P001 allowlist: an arbitrary path now fails loudly instead of
        # silently building a graph outside every registered vault.
        rogue = tmp_vault.parent / "rogue-vault"
        rogue.mkdir(exist_ok=True)
        with pytest.raises(VaultConfigError):
            build_graph.get_vault_root(_ns(vault=rogue))

    def test_env_branch_and_default_branch_untouched(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VAULT_ROOT", str(tmp_vault))
        assert build_graph.get_vault_root(_ns(vault=None)) == tmp_vault
        monkeypatch.delenv("VAULT_ROOT", raising=False)
        assert build_graph.get_vault_root(_ns(vault=None)) == (
            build_graph._default_vault_root()
        )


class TestMainEndToEndNameResolution:
    """build_graph.main() with ``--vault NAME`` must build the named vault."""

    def test_main_with_registered_name(
        self, tmp_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.test_build_graph_parsight import make_embeddings_db

        make_embeddings_db(tmp_vault, [("a", "Patterns"), ("b", "Debugging")])
        out = tmp_path / "graph.json"
        argv = [
            "build_graph.py",
            "--vault",
            "test",
            "--output",
            str(out),
            "--min-threshold",
            "1.01",
            "--no-parsight",
            "--no-schema",
        ]
        monkeypatch.setattr(sys, "argv", argv)
        build_graph.main()
        graph = json.loads(out.read_text(encoding="utf-8"))
        assert {n["id"] for n in graph["nodes"]} == {"a", "b"}
