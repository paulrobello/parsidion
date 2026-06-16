"""Tests for the connect verb, codex feature-flag fix, and instructions injection."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import installer.hooks as hooks  # noqa: E402


def _apply(config: Path, *, yes: bool) -> str:
    """Drive _set_codex_hooks_in_features_section the way enable_codex_hooks_config does."""
    updated = hooks._set_codex_hooks_in_features_section(
        config.read_text(encoding="utf-8"), yes=yes
    )
    assert updated is not None, "installer declined to edit a safe config"
    config.write_text(updated, encoding="utf-8")
    return config.read_text(encoding="utf-8")


class TestCodexFeatureFlagName:
    def test_features_section_uses_hooks_key_not_codex_hooks(
        self, tmp_path: Path
    ) -> None:
        # Empty [features] section -> key is inserted. Exercises the insert branch.
        config = tmp_path / "config.toml"
        config.write_text("[features]\n", encoding="utf-8")
        text = _apply(config, yes=True)
        assert "hooks = true" in text
        assert "codex_hooks" not in text

    def test_existing_false_flag_flipped_to_hooks_true(self, tmp_path: Path) -> None:
        # Pre-existing disabled flag is flipped to true. Exercises the regex branch.
        config = tmp_path / "config.toml"
        config.write_text("[features]\nhooks = false\n", encoding="utf-8")
        text = _apply(config, yes=True)
        assert "hooks = true" in text
        assert "codex_hooks" not in text
