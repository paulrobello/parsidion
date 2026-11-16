"""Secondary-vault registration: ``--vault-name`` / ``--no-set-default``."""

from __future__ import annotations

from pathlib import Path

import pytest

from installer import vault as installer_vault


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str) -> Path:
    home = tmp_path / "home"
    cfg = home / ".config" / "parsidion"
    cfg.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = cfg / "vaults.yaml"
    path.write_text(content, encoding="utf-8")
    return path


_EXISTING = "vaults:\n  main: /main\n\ndefault: /main\n"


def test_record_secondary_vault_without_set_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_path = _setup(tmp_path, monkeypatch, _EXISTING)
    personal = tmp_path / "ParsidionPersonalVault"
    personal.mkdir()

    installer_vault.record_installed_vault(personal, name="personal", set_default=False)

    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    assert f"  personal: {personal}" in lines
    assert "  main: /main" in lines
    assert lines.count("default: /main") == 1
    assert not any(ln.startswith("default:") and ln != "default: /main" for ln in lines)


def test_record_vault_name_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_path = _setup(tmp_path, monkeypatch, _EXISTING)
    personal = tmp_path / "ParsidionPersonalVault"
    personal.mkdir()

    installer_vault.record_installed_vault(personal, name="personal", set_default=False)

    content = yaml_path.read_text(encoding="utf-8")
    assert f"  personal: {personal}" in content
    assert "ParsidionPersonalVault:" not in content


def test_set_default_true_preserves_current_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yaml_path = _setup(tmp_path, monkeypatch, _EXISTING)
    work = tmp_path / "WorkVault"
    work.mkdir()

    installer_vault.record_installed_vault(work)

    lines = yaml_path.read_text(encoding="utf-8").splitlines()
    assert f"  WorkVault: {work}" in lines
    assert f"default: {work}" in lines
    assert "default: /main" not in lines


def test_validate_registered_vault_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch, "")
    vault = tmp_path / "personal"
    vault.mkdir()
    installer_vault.record_installed_vault(vault, name="personal", set_default=False)

    assert installer_vault.validate_installed_vault(vault, "personal") is True
    assert "does not resolve" not in capsys.readouterr().out


def test_validate_registered_vault_warns_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch, "")
    vault = tmp_path / "personal"
    vault.mkdir()

    assert installer_vault.validate_installed_vault(vault, "personal") is False
    out = capsys.readouterr()
    assert "does not resolve" in out.out + out.err


def test_validate_registered_vault_warns_when_name_points_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    _setup(tmp_path, monkeypatch, f"vaults:\n  personal: {other}\n")
    vault = tmp_path / "personal"
    vault.mkdir()

    assert installer_vault.validate_installed_vault(vault, "personal") is False
    out = capsys.readouterr().out
    assert "resolves to" in out
    assert str(other) in out


def test_validate_default_vault_install_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(tmp_path, monkeypatch, "")
    default = installer_vault._default_vault_path()

    assert installer_vault.validate_installed_vault(default, None) is True
    assert capsys.readouterr().out == ""
