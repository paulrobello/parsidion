"""Tests for parsight_backend availability probing + config plumbing (Task 2)."""

from __future__ import annotations

import json
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from core import parsight_backend  # noqa: E402 — ARC-006: patch internals where they live
import vault_common  # noqa: E402
import vault_config  # noqa: E402
import vault_hooks  # noqa: E402

from tests.fake_parsight import FakeHealth, FakeParsight  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_config(vault: Path, text: str) -> None:
    (vault / "config.yaml").write_text(text, encoding="utf-8")
    vault_common.clear_config_cache()
    parsight_backend.reset_parsight_cache()


class TestHealthUrl:
    def test_derives_from_parsight_mcp_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:5555/mcp")
        assert parsight_backend._health_url() == "http://127.0.0.1:5555/health"

    def test_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PARSIGHT_MCP_URL", raising=False)
        assert parsight_backend._health_url() == "http://127.0.0.1:4848/health"

    def test_garbage_url_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PARSIGHT_MCP_URL", "not a url at all")
        assert parsight_backend._health_url() == "http://127.0.0.1:4848/health"


class TestResolveParsightBackend:
    def test_available_when_binary_and_health_ok(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        assert parsight_backend.resolve_parsight_backend() is True
        assert "/health" in fake_parsight_health.requests

    def test_disabled_via_config_skips_probe(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        _write_config(tmp_vault, "parsight:\n  enabled: false\n")
        assert parsight_backend.resolve_parsight_backend() is False
        assert fake_parsight_health.requests == []

    def test_binary_missing(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight_health: FakeHealth,
    ) -> None:
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert parsight_backend.resolve_parsight_backend() is False

    def test_health_down(self, tmp_vault: Path, fake_parsight: FakeParsight) -> None:
        # Autouse isolation pins PARSIGHT_MCP_URL at an unreachable port.
        assert parsight_backend.resolve_parsight_backend() is False

    def test_absolute_binary_path_in_config(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        _write_config(tmp_vault, f"parsight:\n  binary: {fake_parsight.binary_path}\n")
        empty = tmp_path / "empty-bin"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        assert parsight_backend.resolve_parsight_backend() is True

    def test_untrusted_binary_falls_back_to_default(
        self,
        tmp_vault: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        capsys,
    ) -> None:
        """SEC-007: a group/world-writable configured binary is refused.

        A synced config.yaml pointing ``parsight.binary`` at an
        attacker-writable script must not win; the default ``parsight`` from
        PATH is used instead.
        """
        evil = tmp_path / "evil-parsight"
        evil.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        evil.chmod(0o777)  # group+world writable -> untrusted
        _write_config(tmp_vault, f"parsight:\n  binary: {evil}\n")

        assert parsight_backend.resolve_parsight_backend() is True
        assert parsight_backend._resolve_binary(vault=tmp_vault) == str(
            fake_parsight.binary_path
        )
        assert "SEC-007" in capsys.readouterr().err

    def test_legacy_binary_name_falls_back(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        """Compat: when only the legacy ``par-mem`` name is on PATH, it serves."""
        legacy = fake_parsight.bin_dir / "par-mem"
        shutil.copy2(fake_parsight.binary_path, legacy)
        fake_parsight.binary_path.unlink()
        # Isolate PATH to the fake bin dir so a real binary elsewhere cannot
        # satisfy the probe (same discipline as test_result_cached_until_reset).
        monkeypatch.setenv("PATH", str(fake_parsight.bin_dir))
        parsight_backend.reset_parsight_cache()
        assert parsight_backend.resolve_parsight_backend() is True
        assert parsight_backend._resolve_binary(vault=tmp_vault) == str(legacy)

    def test_parsight_preferred_over_legacy_name(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        """Compat: with both names on PATH, the renamed binary wins."""
        legacy = fake_parsight.bin_dir / "par-mem"
        shutil.copy2(fake_parsight.binary_path, legacy)
        monkeypatch.setenv("PATH", str(fake_parsight.bin_dir))
        assert parsight_backend._resolve_binary(vault=tmp_vault) == str(
            fake_parsight.binary_path
        )

    def test_result_cached_until_reset(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        # Isolate PATH to only the fake bin dir: some dev machines have a
        # real parsight binary elsewhere on PATH, which would mask the
        # unlink below and falsely satisfy the post-reset probe.
        monkeypatch.setenv("PATH", str(fake_parsight.bin_dir))
        assert parsight_backend.resolve_parsight_backend() is True
        fake_parsight.binary_path.unlink()
        assert parsight_backend.resolve_parsight_backend() is True  # cached
        parsight_backend.reset_parsight_cache()
        assert parsight_backend.resolve_parsight_backend() is False

    def test_never_raises_on_config_explosion(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("config exploded")

        monkeypatch.setattr(parsight_backend, "_config_value", boom)
        assert parsight_backend.resolve_parsight_backend() is False


class TestTimeoutConfig:
    def test_default(self, tmp_vault: Path) -> None:
        assert parsight_backend._timeout_s(None) == 10.0

    def test_configured(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "parsight:\n  timeout_s: 3\n")
        assert parsight_backend._timeout_s(None) == 3.0

    def test_non_numeric_falls_back(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "parsight:\n  timeout_s: soon\n")
        assert parsight_backend._timeout_s(None) == 10.0


class TestConfigSchema:
    def test_parsight_and_search_sections_registered(self) -> None:
        assert vault_config._CONFIG_SCHEMA["parsight"]["enabled"] == (bool,)
        assert vault_config._CONFIG_SCHEMA["parsight"]["binary"] == (str,)
        assert vault_config._CONFIG_SCHEMA["parsight"]["timeout_s"] == (int, float)
        assert vault_config._CONFIG_SCHEMA["search"]["backend"] == (str,)

    def test_template_config_carries_new_sections(self) -> None:
        template = (
            REPO_ROOT / "skills" / "parsidion" / "templates" / "config.yaml"
        ).read_text(encoding="utf-8")
        parsed = vault_config._parse_config_yaml(template)
        assert parsed["parsight"]["enabled"] is True
        assert parsed["parsight"]["binary"] == "parsight"
        assert parsed["parsight"]["timeout_s"] == 10
        assert parsed["search"]["backend"] == "auto"


class TestLegacyCompat:
    """Pre-rename config spellings (``par_mem`` section, ``par-mem`` backend
    value) keep working — normalized at load time by
    ``vault_config._apply_legacy_aliases``."""

    def test_legacy_par_mem_section_aliased_to_parsight(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "par_mem:\n  binary: /opt/legacy\n  timeout_s: 4\n")
        config = vault_common.load_config()
        assert "par_mem" not in config
        assert config["parsight"]["binary"] == "/opt/legacy"
        assert config["parsight"]["timeout_s"] == 4
        assert parsight_backend._timeout_s(None) == 4.0

    def test_canonical_parsight_wins_per_key(self, tmp_vault: Path) -> None:
        _write_config(
            tmp_vault,
            "parsight:\n  binary: /opt/new\npar_mem:\n  binary: /opt/old\n"
            "  timeout_s: 6\n",
        )
        config = vault_common.load_config()
        assert config["parsight"]["binary"] == "/opt/new"  # canonical wins
        assert config["parsight"]["timeout_s"] == 6  # legacy fills absent keys

    def test_legacy_backend_enum_value_normalized(self, tmp_vault: Path) -> None:
        _write_config(tmp_vault, "search:\n  backend: par-mem\n")
        assert vault_common.load_config()["search"]["backend"] == "parsight"
        assert vault_config.load_typed_config().search.backend == "parsight"

    def test_local_overlay_wins_across_alias(self, tmp_vault: Path) -> None:
        """A legacy ``par_mem`` key in config.local.yaml overrides the
        canonical ``parsight`` key in config.yaml (overlay precedence holds
        because each file is normalized before the merge)."""
        _write_config(tmp_vault, "parsight:\n  binary: /opt/base\n")
        (tmp_vault / "config.local.yaml").write_text(
            "par_mem:\n  binary: /opt/local\n", encoding="utf-8"
        )
        vault_common.clear_config_cache()
        assert vault_common.load_config()["parsight"]["binary"] == "/opt/local"


class TestPackaging:
    def test_parsight_backend_registered_in_py_modules(self) -> None:
        pyproject = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert "parsight_backend" in pyproject["tool"]["setuptools"]["py-modules"]


class TestDocLinksRaw:
    def test_doc_links_raw_happy_path(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        links = [
            {
                "source_path": "Debugging/a.md",
                "target_path": "Patterns/b.md",
                "target_is_doc": True,
                "count": 2,
            }
        ]
        fake_parsight.configure(
            doc_links={"links": links, "total": 1, "truncated": False}
        )
        result = parsight_backend.doc_links_raw(vault=tmp_vault)
        assert result == links
        call = fake_parsight.wait_for_call("doc-links")
        assert call["argv"] == [
            "doc-links",
            "--json",
            "--targets",
            "doc",
            "--limit",
            "200000",
        ]

    def test_doc_links_raw_truncated_returns_links_and_logs(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        links = [
            {
                "source_path": "Debugging/a.md",
                "target_path": "Patterns/b.md",
                "target_is_doc": True,
                "count": 2,
            }
        ]
        fake_parsight.configure(
            doc_links={"links": links, "total": 200500, "truncated": True}
        )
        result = parsight_backend.doc_links_raw(vault=tmp_vault)
        assert result == links
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["hook"] == "ParsightBackend"
        assert event["detail"].startswith("truncated:200500")

    def test_doc_links_raw_nonzero_exit_returns_none(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        fake_parsight.configure(exit_codes={"doc-links": 1})
        assert parsight_backend.doc_links_raw(vault=tmp_vault) is None
        log = (tmp_vault / "hook_events.log").read_text(encoding="utf-8")
        event = json.loads(log.strip().splitlines()[-1])
        assert event["hook"] == "ParsightBackend"
        assert event["detail"].startswith("exit:1")

    def test_doc_links_raw_bad_json_returns_none(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        fake_parsight.configure(stdout_override="not json")
        assert parsight_backend.doc_links_raw(vault=tmp_vault) is None

    def test_doc_links_raw_missing_links_key_returns_none(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
    ) -> None:
        fake_parsight.configure(doc_links={"total": 0})
        assert parsight_backend.doc_links_raw(vault=tmp_vault) is None

    def test_doc_links_raw_unavailable_returns_none(
        self, tmp_vault: Path, fake_parsight: FakeParsight
    ) -> None:
        # No health fixture: autouse isolation points at an unreachable port.
        assert parsight_backend.doc_links_raw(vault=tmp_vault) is None
        fake_parsight.assert_no_call("doc-links", settle=0.1)


class TestSafeEnv:
    def test_parsight_mcp_url_passes_through_child_env(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert "PARSIGHT_MCP_URL" in vault_hooks.SAFE_ENV_KEYS
        monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:6666/mcp")
        env = vault_common.env_without_claudecode(vault=tmp_vault)
        assert env["PARSIGHT_MCP_URL"] == "http://127.0.0.1:6666/mcp"


class TestParsightEnvAllowlist:
    """SEC-206: parsight subprocesses get the ``_PARSIGHT_ENV_KEYS`` allowlist,
    not the full ``_SAFE_ENV_KEYS`` set the claude -p path forwards — the
    parsight CLI talks to a local daemon and must never receive Anthropic
    credentials."""

    def test_parsight_env_helper_is_allowlist(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
        monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:6666/mcp")
        env = parsight_backend._parsight_env()
        assert set(env) <= set(parsight_backend._PARSIGHT_ENV_KEYS)
        assert env["PARSIGHT_MCP_URL"] == "http://127.0.0.1:6666/mcp"
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_AUTH_TOKEN" not in env

    def test_run_parsight_child_env_omits_anthropic_keys(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end through _run_parsight: the fake binary records its
        actual environment names; the Anthropic keys set in the parent must
        not appear there."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
        assert parsight_backend.doc_links_raw(vault=tmp_vault) is not None
        call = fake_parsight.wait_for_call("doc-links")
        env_keys = call.get("env_keys")
        assert isinstance(env_keys, list)
        assert "ANTHROPIC_API_KEY" not in env_keys
        assert "ANTHROPIC_AUTH_TOKEN" not in env_keys
        assert "PATH" in env_keys
        assert "HOME" in env_keys

    def test_spawn_background_index_child_env_omits_anthropic_keys(
        self,
        tmp_vault: Path,
        fake_parsight: FakeParsight,
        fake_parsight_health: FakeHealth,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-test")
        assert parsight_backend.spawn_background_index(vault=tmp_vault) is True
        call = fake_parsight.wait_for_call("index")
        env_keys = call.get("env_keys")
        assert isinstance(env_keys, list)
        assert "ANTHROPIC_API_KEY" not in env_keys
        assert "ANTHROPIC_AUTH_TOKEN" not in env_keys
        assert "PARSIGHT_MCP_URL" in env_keys
