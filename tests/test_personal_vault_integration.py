"""Two-vault scenario: main vault plus a personal vault with its own taxonomy."""

from __future__ import annotations

from pathlib import Path

import pytest

from installer import vault as installer_vault
from parsidion.core import vault_path
from parsidion.core.vault_config import clear_config_cache, resolve_taxonomy
from parsidion.core.vault_fs import ensure_vault_dirs

_PERSONAL_CONFIG = (
    "taxonomy:\n"
    "  folders: Home, Pets, Reference, Daily\n"
    "  type_folder.knowledge: Reference\n"
    "  default_folder: Reference\n"
)


def test_two_vault_personal_taxonomy_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / ".config" / "parsidion").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ("CLAUDE_VAULT", "VAULT_ROOT", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_path.drain_resolution_warnings()
    clear_config_cache()

    main = tmp_path / "MainVault"
    personal = tmp_path / "PersonalVault"
    main.mkdir()
    personal.mkdir()

    # Bootstrap: main is the default, personal is registered without taking over.
    installer_vault.record_installed_vault(main, name="main")
    ensure_vault_dirs(main)
    (personal / "config.yaml").write_text(_PERSONAL_CONFIG, encoding="utf-8")
    for engineering in ("Patterns", "Debugging", "Frameworks"):
        (personal / engineering).mkdir()
    installer_vault.record_installed_vault(personal, name="personal", set_default=False)
    clear_config_cache()
    ensure_vault_dirs(personal)

    # (a) default still resolves to the main vault.
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    assert vault_path.resolve_vault(cwd=home) == main

    # (b) a project pointing at "personal" resolves the secondary vault.
    project = tmp_path / "proj"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "vault").write_text("personal\n", encoding="utf-8")
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    assert vault_path.resolve_vault(cwd=project) == personal

    # (c) engineering folders were pruned, taxonomy folders exist.
    for engineering in ("Patterns", "Debugging", "Frameworks"):
        assert not (personal / engineering).exists()
    for folder in ("Home", "Pets", "Reference", "Daily"):
        assert (personal / folder).is_dir()
    assert (main / "Patterns").is_dir()

    # (d) knowledge notes route to Reference in the personal vault.
    clear_config_cache()
    taxonomy = resolve_taxonomy(personal)
    assert taxonomy.type_folders["knowledge"] == "Reference"
    assert taxonomy.default_folder == "Reference"

    # (e) the happy path produced no resolution warnings.
    assert vault_path.drain_resolution_warnings() == []


def test_work_session_receives_no_personal_vault_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec anti-goal: a work-repo SessionStart never sees personal notes."""
    import io
    import json
    import sys

    import session_start_hook

    home = tmp_path / "home"
    (home / ".config" / "parsidion").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for var in ("CLAUDE_VAULT", "VAULT_ROOT", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)
    vault_path.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    clear_config_cache()

    main = tmp_path / "MainVault"
    personal = tmp_path / "PersonalVault"
    main.mkdir()
    personal.mkdir()
    installer_vault.record_installed_vault(main, name="main")
    ensure_vault_dirs(main)
    (personal / "config.yaml").write_text(_PERSONAL_CONFIG, encoding="utf-8")
    installer_vault.record_installed_vault(personal, name="personal", set_default=False)
    clear_config_cache()
    ensure_vault_dirs(personal)

    frontmatter = "---\ndate: 2026-11-16\ntype: {t}\ntags: [{tag}]\nproject: workrepo\nconfidence: high\nsources: []\nrelated: []\n---\n"
    (main / "Patterns" / "work-shared-pattern.md").write_text(
        frontmatter.format(t="pattern", tag="work") + "# Work Pattern\nwork body\n",
        encoding="utf-8",
    )
    (personal / "Reference" / "secret-boiler-manual.md").write_text(
        frontmatter.format(t="knowledge", tag="home")
        + "# Boiler Manual\nPERSONAL-ONLY-CONTENT\n",
        encoding="utf-8",
    )
    (main / "config.yaml").write_text(
        "session_start_hook:\n  ai_model: null\n  track_delta: false\n"
        "  use_embeddings: false\n",
        encoding="utf-8",
    )
    clear_config_cache()

    work = tmp_path / "workrepo"
    work.mkdir()
    events: list[dict[str, object]] = []
    monkeypatch.setattr(
        session_start_hook, "write_hook_event", lambda **kw: events.append(kw)
    )
    monkeypatch.setattr(
        session_start_hook.parsight_backend, "spawn_watch", lambda *a: True
    )
    monkeypatch.setattr(sys, "argv", ["session_start_hook.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"cwd": str(work)})))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)

    session_start_hook.main()

    context = json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]
    assert "work-shared-pattern" in context
    assert "secret-boiler-manual" not in context
    assert "PERSONAL-ONLY-CONTENT" not in context
    assert "Boiler" not in context
