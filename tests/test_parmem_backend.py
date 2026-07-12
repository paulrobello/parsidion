"""Tests for parmem_backend availability probing + config plumbing (Task 2)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import parmem_backend  # noqa: E402
import vault_common  # noqa: E402
import vault_config  # noqa: E402
import vault_hooks  # noqa: E402

from tests.fake_parmem import FakeHealth, FakeParMem  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.load_config.cache_clear()
    parmem_backend.reset_parmem_cache()


class TestHealthUrl:
    def test_derives_from_parmem_mcp_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PARMEM_MCP_URL", "http://127.0.0.1:5555/mcp")
        assert parmem_backend._health_url() == "http://127.0.0.1:5555/health"

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PARMEM_MCP_URL", raising=False)
        assert parmem_backend._health_url() == "http://127.0.0.1:4848/health"

    def test_garbage_url_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PARMEM_MCP_URL", "not a url at all")
        assert parmem_backend._health_url() == "http://127.0.0.1:4848/health"


class TestResolveParmemBackend:
    def test_available_when_binary_and_health_ok(
        self, tmp_vault: Path, fake_parmem: FakeParMem, fake_parmem_health: FakeHealth
    ) -> None:
        assert parmem_backend.resolve_parmem_backend() is True
        assert "/health" in fake_parmem_health.requests

    def test_disabled_via_config_skips_probe(
        self, tmp_vault: Path, fake_parmem: FakeParMem, fake_parmem_health: FakeHealth
    ) -> None:
        _write_config(tmp_vault, "par_mem:\n  enabled: false\n")
        assert parmem_backend.resolve_parmem_backend() is False
        assert fake_parmem_health.requests == []

    def test_binary_missing(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parmem_health: FakeHealth,
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert parmem_backend.resolve_parmem_backend() is False

    def test_health_down(self, tmp_vault: Path, fake_parmem: FakeParMem) -> None:
        # Autouse isolation pins PARMEM_MCP_URL at an unreachable port.
        assert parmem_backend.resolve_parmem_backend() is False

    def test_absolute_binary_path_in_config(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
    ) -> None:
        _write_config(tmp_vault, f"par_mem:\n  binary: {fake_parmem.binary_path}\n")
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert parmem_backend.resolve_parmem_backend() is True

    def test_result_cached_until_reset(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parmem: FakeParMem,
        fake_parmem_health: FakeHealth,
    ) -> None:
        # Isolate PATH to only the fake bin dir: some dev machines have a
        # real par-mem binary elsewhere on PATH, which would mask the
        # unlink below and falsely satisfy the post-reset probe.
        monkeypatch.setenv("PATH", str(fake_parmem.bin_dir))
        assert parmem_backend.resolve_parmem_backend() is True
        fake_parmem.binary_path.unlink()
        assert parmem_backend.resolve_parmem_backend() is True  # cached
        parmem_backend.reset_parmem_cache()
        assert parmem_backend.resolve_parmem_backend() is False

    def test_never_raises_on_config_explosion(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("config exploded")

        monkeypatch.setattr(parmem_backend, "_config_value", boom)
        assert parmem_backend.resolve_parmem_backend() is False


class TestTimeoutConfig:
    def test_default(self, tmp_vault: Path) -> None:
        assert parmem_backend._timeout_s(None) == 10.0

    def test_configured(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "par_mem:\n  timeout_s: 3\n")
        assert parmem_backend._timeout_s(None) == 3.0

    def test_non_numeric_falls_back(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "par_mem:\n  timeout_s: soon\n")
        assert parmem_backend._timeout_s(None) == 10.0


class TestConfigSchema:
    def test_par_mem_and_search_sections_registered(self) -> None:
        assert vault_config._CONFIG_SCHEMA["par_mem"]["enabled"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["par_mem"]["binary"] == (str,)
        assert vault_config._CONFIG_SCHEMA["par_mem"]["timeout_s"] == (int, float)
        assert vault_config._CONFIG_SCHEMA["search"]["backend"] == (str,)

    def test_template_config_carries_new_sections(self) -> None:
        template = (
            REPO_ROOT / "skills" / "parsidion" / "templates" / "config.yaml"
        ).read_text(encoding="utf-8")
        parsed = vault_config._parse_config_yaml(template)
        assert parsed["par_mem"]["enabled"] is True
        assert parsed["par_mem"]["binary"] == "par-mem"
        assert parsed["par_mem"]["timeout_s"] == 10
        assert parsed["search"]["backend"] == "auto"


class TestPackaging:
    def test_parmem_backend_registered_in_py_modules(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert "parmem_backend" in pyproject["tool"]["setuptools"]["py-modules"]


class TestSafeEnv:
    def test_parmem_mcp_url_passes_through_child_env(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert "PARMEM_MCP_URL" in vault_hooks.SAFE_ENV_KEYS
        monkeypatch.setenv("PARMEM_MCP_URL", "http://127.0.0.1:6666/mcp")
        env = vault_common.env_without_claudecode(vault=tmp_vault)
        assert env["PARMEM_MCP_URL"] == "http://127.0.0.1:6666/mcp"
