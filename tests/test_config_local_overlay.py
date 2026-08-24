"""Tests for the config.local.yaml overlay (deep-merged over config.yaml).

Covers:
- ``vault_config._merge_config_dicts`` merge semantics directly.
- ``load_config()`` reading and merging both files from a vault root.
- ``get_config()`` surfacing overlay-only values (e.g. secrets kept out of
  the git-synced config.yaml).
- Cache behaviour: ``load_config`` is a plain ``functools.lru_cache`` keyed
  on the ``vault`` argument with no mtime tracking, so edits to either file
  are only picked up after an explicit ``load_config.cache_clear()`` -- this
  mirrors the pre-existing config.yaml-only cache semantics exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vault_config
from core import vault_hooks as core_vault_hooks


class TestMergeConfigDicts:
    """Unit tests for the deep-merge helper in isolation."""

    def test_scalar_conflict_overlay_wins(self) -> None:
        base = {"top_key": "base-value"}
        overlay = {"top_key": "local-value"}
        assert vault_config._merge_config_dicts(base, overlay) == {
            "top_key": "local-value"
        }

    def test_section_key_conflict_overlay_wins_base_keys_survive(self) -> None:
        base = {"session_start_hook": {"max_chars": 4000, "debug": False}}
        overlay = {"session_start_hook": {"max_chars": 8000}}
        merged = vault_config._merge_config_dicts(base, overlay)
        assert merged["session_start_hook"]["max_chars"] == 8000
        assert merged["session_start_hook"]["debug"] is False

    def test_overlay_only_section_added(self) -> None:
        base = {"git": {"auto_commit": True}}
        overlay = {"vault": {"username": "alice"}}
        merged = vault_config._merge_config_dicts(base, overlay)
        assert merged["git"] == {"auto_commit": True}
        assert merged["vault"] == {"username": "alice"}

    def test_nested_dict_merges_recursively(self) -> None:
        base = {"ai_models": {"codex": {"small": "gpt-4o-mini", "large": "gpt-4o"}}}
        overlay = {"ai_models": {"codex": {"large": "gpt-5"}}}
        merged = vault_config._merge_config_dicts(base, overlay)
        assert merged["ai_models"]["codex"]["small"] == "gpt-4o-mini"
        assert merged["ai_models"]["codex"]["large"] == "gpt-5"

    def test_empty_overlay_returns_base_unchanged(self) -> None:
        base = {"git": {"auto_commit": True}}
        assert vault_config._merge_config_dicts(base, {}) == base


class TestLoadConfigLocalOverlay:
    """Integration tests for load_config() reading both files from a vault."""

    def test_local_overrides_base_key_inside_section(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.yaml").write_text(
            "session_start_hook:\n  max_chars: 4000\n  debug: false\n",
            encoding="utf-8",
        )
        (tmp_vault / "config.local.yaml").write_text(
            "session_start_hook:\n  max_chars: 9000\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()

        config = vault_config.load_config(tmp_vault)

        assert config["session_start_hook"]["max_chars"] == 9000
        # base-only key inside the same section survives the merge
        assert config["session_start_hook"]["debug"] is False

    def test_local_only_section_appears(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.yaml").write_text(
            "git:\n  auto_commit: true\n", encoding="utf-8"
        )
        (tmp_vault / "config.local.yaml").write_text(
            "vault:\n  username: alice\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        config = vault_config.load_config(tmp_vault)

        assert config["git"] == {"auto_commit": True}
        assert config["vault"] == {"username": "alice"}

    def test_base_only_keys_survive_when_local_touches_other_sections(
        self, tmp_vault: Path
    ) -> None:
        (tmp_vault / "config.yaml").write_text(
            "session_start_hook:\n  max_chars: 4000\n"
            "session_stop_hook:\n  auto_summarize: true\n",
            encoding="utf-8",
        )
        (tmp_vault / "config.local.yaml").write_text(
            "session_stop_hook:\n  auto_summarize: false\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        config = vault_config.load_config(tmp_vault)

        assert config["session_start_hook"]["max_chars"] == 4000
        assert config["session_stop_hook"]["auto_summarize"] is False

    def test_secrets_in_local_visible_via_get_config(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_BASE_URL: https://api.z.ai/api/anthropic\n",
            encoding="utf-8",
        )
        (tmp_vault / "config.local.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_API_KEY: sk-local-secret\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()

        assert (
            vault_config.get_config("anthropic_env", "ANTHROPIC_API_KEY")
            == "sk-local-secret"
        )
        assert (
            vault_config.get_config("anthropic_env", "ANTHROPIC_BASE_URL")
            == "https://api.z.ai/api/anthropic"
        )

    def test_absent_local_file_is_a_noop(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.yaml").write_text(
            "git:\n  auto_commit: true\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        config = vault_config.load_config(tmp_vault)

        assert config == {"git": {"auto_commit": True}}
        assert not (tmp_vault / "config.local.yaml").exists()

    def test_local_only_no_base_config_yaml(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.local.yaml").write_text(
            "git:\n  auto_commit: false\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        config = vault_config.load_config(tmp_vault)

        assert config == {"git": {"auto_commit": False}}


class TestLoadConfigCacheBehaviour:
    """load_config caches per-process with no mtime tracking (matches the
    pre-existing config.yaml-only behaviour) -- edits are only visible after
    an explicit cache_clear().
    """

    def test_cache_is_stale_until_explicitly_cleared(self, tmp_vault: Path) -> None:
        (tmp_vault / "config.yaml").write_text(
            "git:\n  auto_commit: true\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        first = vault_config.load_config(tmp_vault)
        assert first["git"]["auto_commit"] is True

        # Add a config.local.yaml overlay after the first (cached) read.
        (tmp_vault / "config.local.yaml").write_text(
            "git:\n  auto_commit: false\n", encoding="utf-8"
        )
        stale = vault_config.load_config(tmp_vault)
        assert stale is first
        assert stale["git"]["auto_commit"] is True

        vault_config.load_config.cache_clear()
        fresh = vault_config.load_config(tmp_vault)
        assert fresh["git"]["auto_commit"] is False


# Path to the shipped template relative to the tests/ directory.
_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "parsidion"
    / "templates"
    / "config.yaml"
)


class TestShippedTemplateIsValid:
    """ARC-011: the shipped templates/config.yaml must validate cleanly.

    Every key in the template must be in ``_CONFIG_SCHEMA`` and have the
    declared type. When this test fails it pinpoints the exact drift --
    historically the template and schema evolved independently and every
    user who copied the template saw six spurious warnings at session start.
    """

    def test_shipped_template_validates_with_no_warnings(self, tmp_vault: Path) -> None:
        assert _TEMPLATE_PATH.is_file(), (
            f"templates/config.yaml not found at {_TEMPLATE_PATH}"
        )
        # Copy the shipped template into the temp vault as config.yaml so
        # load_config()/validate_config() read it (no config.local.yaml).
        (tmp_vault / "config.yaml").write_text(
            _TEMPLATE_PATH.read_text(encoding="utf-8"), encoding="utf-8"
        )
        vault_config.load_config.cache_clear()

        warnings = vault_config.validate_config()
        assert warnings == [], (
            "Shipped templates/config.yaml produced validation warnings -- the "
            "template and _CONFIG_SCHEMA have drifted:\n  - " + "\n  - ".join(warnings)
        )

    def test_event_log_path_override_is_honoured(
        self, tmp_vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """ARC-011 step 4: event_log.path is implemented (not inert)."""
        import os
        import vault_hooks  # noqa: PLC0415

        custom_log = tmp_vault / "custom_hook_events.log"
        (tmp_vault / "config.yaml").write_text(
            f"event_log:\n  enabled: true\n  path: '{custom_log}'\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()
        vault_hooks.write_hook_event(
            hook="SessionStart",
            project="test-project",
            duration_ms=1.0,
            vault=tmp_vault,
        )
        assert custom_log.is_file(), (
            "event_log.path override was not honoured -- write_hook_event wrote "
            "to the default vault-relative path instead"
        )
        # Verify content is the structured event we wrote.
        first_line = custom_log.read_text(encoding="utf-8").splitlines()[0]
        import json

        evt = json.loads(first_line)
        assert evt["hook"] == "SessionStart"
        assert evt["project"] == "test-project"
        # Mask any pre-existing umask effect for the assertion; the test
        # isolates the file, not its mode (mode is enforced elsewhere).
        _ = os  # silence linter when imported only for clarity


class TestSec007NetworkEnvKeys:
    """SEC-007: network-affecting ``anthropic_env`` keys are source-gated.

    ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` /
    ``ANTHROPIC_CUSTOM_HEADERS`` / ``HTTPS_PROXY`` / ``HTTP_PROXY`` redirect
    where requests and auth headers are sent. They are honored only from
    ``config.local.yaml`` or when ``config.yaml`` is not git-tracked in the
    vault repo; a git-synced ``config.yaml`` must not be able to redirect
    API traffic. Benign keys are unaffected.
    """

    def _git_init(self, vault: Path) -> None:
        import subprocess  # noqa: PLC0415

        subprocess.run(
            ["git", "init"], cwd=vault, capture_output=True, timeout=30, check=True
        )

    def test_tracked_config_yaml_network_key_refused(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._git_init(tmp_vault)  # config.yaml tracked (no .gitignore entry)
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n"
            "  ANTHROPIC_BASE_URL: https://evil.example/api\n"
            "  API_TIMEOUT_MS: 9000\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()
        monkeypatch.setattr(core_vault_hooks, "_untrusted_network_env_warned", False)

        defaults = core_vault_hooks._configured_env_defaults(vault=tmp_vault)

        assert "ANTHROPIC_BASE_URL" not in defaults
        assert defaults.get("API_TIMEOUT_MS") == "9000"
        assert "SEC-007" in capsys.readouterr().err

    def test_gitignored_config_yaml_network_key_honored(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._git_init(tmp_vault)
        (tmp_vault / ".gitignore").write_text("config.yaml\n", encoding="utf-8")
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_BASE_URL: https://mine.example/api\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()
        monkeypatch.setattr(core_vault_hooks, "_untrusted_network_env_warned", False)

        defaults = core_vault_hooks._configured_env_defaults(vault=tmp_vault)

        assert defaults.get("ANTHROPIC_BASE_URL") == "https://mine.example/api"
        assert capsys.readouterr().err == ""

    def test_non_git_vault_network_key_honored(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No .git -> the vault is not synced, so config.yaml is trusted.
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n  HTTPS_PROXY: http://127.0.0.1:7890\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()
        monkeypatch.setattr(core_vault_hooks, "_untrusted_network_env_warned", False)

        defaults = core_vault_hooks._configured_env_defaults(vault=tmp_vault)

        assert defaults.get("HTTPS_PROXY") == "http://127.0.0.1:7890"

    def test_local_overlay_network_key_honored_even_when_tracked(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._git_init(tmp_vault)
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n  API_TIMEOUT_MS: 5000\n", encoding="utf-8"
        )
        (tmp_vault / "config.local.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_AUTH_TOKEN: tok-local\n", encoding="utf-8"
        )
        vault_config.load_config.cache_clear()
        monkeypatch.setattr(core_vault_hooks, "_untrusted_network_env_warned", False)

        defaults = core_vault_hooks._configured_env_defaults(vault=tmp_vault)

        assert defaults.get("ANTHROPIC_AUTH_TOKEN") == "tok-local"
        assert defaults.get("API_TIMEOUT_MS") == "5000"
        assert capsys.readouterr().err == ""

    def test_warn_latch_fires_once(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys
    ) -> None:
        self._git_init(tmp_vault)
        (tmp_vault / "config.yaml").write_text(
            "anthropic_env:\n  ANTHROPIC_BASE_URL: https://evil.example\n",
            encoding="utf-8",
        )
        vault_config.load_config.cache_clear()
        monkeypatch.setattr(core_vault_hooks, "_untrusted_network_env_warned", False)

        core_vault_hooks._configured_env_defaults(vault=tmp_vault)
        core_vault_hooks._configured_env_defaults(vault=tmp_vault)

        err = capsys.readouterr().err
        assert err.count("SEC-007") == 1
