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

import vault_config


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
