"""vault-new routes new notes by the vault's resolved taxonomy."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from parsidion.core.vault_config import clear_config_cache
from parsidion.vault_new import main


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["vault-new", *argv])
    main()


def test_vault_new_routes_knowledge_to_reference_in_personal_taxonomy(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_vault / "config.yaml").write_text(
        "taxonomy:\n"
        "  folders: Home, Reference\n"
        "  type_folder.knowledge: Reference\n"
        "  default_folder: Home\n",
        encoding="utf-8",
    )
    clear_config_cache()
    _run(monkeypatch, "--type", "knowledge", "--title", "Boiler Manual")
    assert (tmp_vault / "Reference" / "boiler-manual.md").is_file()


def test_vault_new_default_route_without_taxonomy(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_config_cache()
    _run(monkeypatch, "--type", "pattern", "--title", "Plain Pattern")
    assert (tmp_vault / "Patterns" / "plain-pattern.md").is_file()


def test_vault_new_type_folder_escape_writes_nothing_outside_vault(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_vault / "config.yaml").write_text(
        "taxonomy:\n  folders: Home\n  type_folder.knowledge: ../escape\n",
        encoding="utf-8",
    )
    clear_config_cache()
    outside = tmp_vault.parent / "escape"
    _run(
        monkeypatch,
        "--vault",
        str(tmp_vault),
        "--type",
        "knowledge",
        "--title",
        "Escape Probe",
    )
    assert not outside.exists()
    assert not list(tmp_vault.parent.glob("escape/*"))
