"""Per-vault taxonomy resolution (taxonomy.folders / type_folder.* / default_folder)."""

from __future__ import annotations

from pathlib import Path

import pytest

from parsidion.core.vault_config import clear_config_cache, resolve_taxonomy
from parsidion.core.vault_path import VAULT_DIRS
from parsidion.note_schema import DEFAULT_FOLDER, TYPE_FOLDERS


def _write_config(vault: Path, body: str) -> None:
    (vault / "config.yaml").write_text(body, encoding="utf-8")
    clear_config_cache()


def test_taxonomy_absent_returns_builtins(tmp_vault: Path) -> None:
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == list(VAULT_DIRS)
    assert tax.type_folders == dict(TYPE_FOLDERS)
    assert tax.default_folder == DEFAULT_FOLDER


def test_folders_comma_list_parsed(tmp_vault: Path) -> None:
    _write_config(tmp_vault, "taxonomy:\n  folders: Home, Pets , Appliances,,\n")
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == ["Home", "Pets", "Appliances"]


def test_type_folder_dotted_keys_override(tmp_vault: Path) -> None:
    _write_config(
        tmp_vault,
        "taxonomy:\n"
        "  folders: Home, Reference, Daily\n"
        "  type_folder.knowledge: Reference\n"
        "  type_folder.project: Home\n"
        "  default_folder: Home\n",
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.type_folders["knowledge"] == "Reference"
    assert tax.type_folders["project"] == "Home"
    assert tax.type_folders["daily"] == "Daily"
    assert tax.default_folder == "Home"


def test_unlisted_type_falls_back_to_default_folder_when_builtin_folder_not_in_list(
    tmp_vault: Path,
) -> None:
    _write_config(
        tmp_vault,
        "taxonomy:\n  folders: Home, Daily\n  default_folder: Home\n",
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.type_folders["daily"] == "Daily"
    assert tax.type_folders["debugging"] == "Home"
    assert set(tax.type_folders) == set(TYPE_FOLDERS)


def test_default_folder_invalid_falls_back_to_first_folder(tmp_vault: Path) -> None:
    _write_config(
        tmp_vault,
        "taxonomy:\n  folders: Home, Pets\n  default_folder: Nowhere\n",
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.default_folder == "Home"


def test_config_local_overlay_taxonomy_merged(tmp_vault: Path) -> None:
    _write_config(tmp_vault, "taxonomy:\n  default_folder: Home\n")
    (tmp_vault / "config.local.yaml").write_text(
        "taxonomy:\n  folders: Home, Pets\n", encoding="utf-8"
    )
    clear_config_cache()
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == ["Home", "Pets"]
    assert tax.default_folder == "Home"


def test_overrides_without_folders_are_ignored(tmp_vault: Path) -> None:
    _write_config(
        tmp_vault,
        "taxonomy:\n  type_folder.knowledge: Reference\n  default_folder: Home\n",
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == list(VAULT_DIRS)
    assert tax.type_folders == dict(TYPE_FOLDERS)
    assert tax.default_folder == DEFAULT_FOLDER


SYSTEM_DIRS = {"Daily", "Templates", "History"}


def _top_dirs(vault: Path) -> set[str]:
    return {
        p.name for p in vault.iterdir() if p.is_dir() and not p.name.startswith(".")
    }


def test_ensure_vault_dirs_without_taxonomy_creates_standard_dirs(
    tmp_vault: Path,
) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs

    ensure_vault_dirs(tmp_vault)
    assert _top_dirs(tmp_vault) == set(VAULT_DIRS)
    assert (tmp_vault / "Templates").exists()


def test_ensure_vault_dirs_with_taxonomy_creates_taxonomy_folders(
    tmp_vault: Path,
) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs

    _write_config(tmp_vault, "taxonomy:\n  folders: Home, Pets\n")
    ensure_vault_dirs(tmp_vault)
    assert _top_dirs(tmp_vault) == {"Home", "Pets"} | SYSTEM_DIRS


def test_prune_removes_empty_standard_folders_when_taxonomy_set(
    tmp_vault: Path,
) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs

    ensure_vault_dirs(tmp_vault)
    _write_config(tmp_vault, "taxonomy:\n  folders: Home, Pets\n")
    ensure_vault_dirs(tmp_vault)
    assert _top_dirs(tmp_vault) == {"Home", "Pets"} | SYSTEM_DIRS


def test_prune_skips_nonempty_folders(tmp_vault: Path) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs

    ensure_vault_dirs(tmp_vault)
    (tmp_vault / "Patterns" / "keep.md").write_text("x", encoding="utf-8")
    (tmp_vault / "Tools" / "sub").mkdir()
    _write_config(tmp_vault, "taxonomy:\n  folders: Home\n")
    ensure_vault_dirs(tmp_vault)
    assert (tmp_vault / "Patterns" / "keep.md").exists()
    assert (tmp_vault / "Tools" / "sub").is_dir()
    assert not (tmp_vault / "Projects").exists()


def test_prune_never_touches_system_folders(tmp_vault: Path) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs
    from parsidion.core.vault_path import EXCLUDE_DIRS

    ensure_vault_dirs(tmp_vault)
    for name in EXCLUDE_DIRS - {"Templates"}:
        (tmp_vault / name).mkdir(exist_ok=True)
    _write_config(tmp_vault, "taxonomy:\n  folders: Home\n")
    ensure_vault_dirs(tmp_vault)
    for name in SYSTEM_DIRS | EXCLUDE_DIRS:
        assert (tmp_vault / name).exists(), name


def test_daily_stays_in_daily_when_taxonomy_omits_it(tmp_vault: Path) -> None:
    _write_config(
        tmp_vault, "taxonomy:\n  folders: Home, Pets\n  default_folder: Home\n"
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.type_folders["daily"] == "Daily"


def test_invalid_folder_names_dropped_with_warning(
    tmp_vault: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_config(
        tmp_vault,
        "taxonomy:\n  folders: Home, ../escape, .hidden, a/b, Templates, .git\n"
        "  type_folder.knowledge: ../escape\n",
    )
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == ["Home"]
    assert tax.type_folders["knowledge"] == "Home"
    err = capsys.readouterr().err
    assert "../escape" in err


def test_scalar_taxonomy_section_falls_back_to_builtins(tmp_vault: Path) -> None:
    from parsidion.core.vault_fs import ensure_vault_dirs

    _write_config(tmp_vault, "taxonomy: Home\n")
    tax = resolve_taxonomy(tmp_vault)
    assert tax.folders == list(VAULT_DIRS)
    ensure_vault_dirs(tmp_vault)
