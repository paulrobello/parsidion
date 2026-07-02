"""Tests for three vault_index.py / update_index.py enhancements.

Covers:
- Frontmatter parse-warning collector (record_parse_warning/drain_parse_warnings)
  so stderr-only warnings become visible via hook events (`vault-stats --hooks N`).
- Non-ASCII-safe slugify (transliteration + stable-hash fallback for titles
  that transliterate to nothing, e.g. CJK-only titles).
- Race-free singleton guard in update_index.py (atomic O_CREAT|O_EXCL claim
  instead of check-then-write).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

import pytest

import update_index
import vault_index

# ---------------------------------------------------------------------------
# Parse warning collector
# ---------------------------------------------------------------------------


class TestParseWarningCollector:
    """Tests for vault_index.record_parse_warning / drain_parse_warnings."""

    @pytest.fixture(autouse=True)
    def _clear_collector(self) -> Generator[None]:
        # Ensure no warnings leak in/out across tests (module-level list).
        vault_index.drain_parse_warnings()
        yield
        vault_index.drain_parse_warnings()

    def test_record_and_drain(self) -> None:
        vault_index.record_parse_warning("warning one")
        vault_index.record_parse_warning("warning two")
        assert vault_index.drain_parse_warnings() == ["warning one", "warning two"]

    def test_drain_clears_collector(self) -> None:
        vault_index.record_parse_warning("warning")
        vault_index.drain_parse_warnings()
        assert vault_index.drain_parse_warnings() == []

    def test_cap_bounds_memory(self) -> None:
        for i in range(vault_index._PARSE_WARNINGS_MAX + 50):
            vault_index.record_parse_warning(f"warning {i}")
        drained = vault_index.drain_parse_warnings()
        assert len(drained) == vault_index._PARSE_WARNINGS_MAX
        assert drained[0] == "warning 0"

    def test_nested_mapping_warning_recorded(self) -> None:
        content = "---\nkey:\n  nested: value\n---\n"
        vault_index.parse_frontmatter(content)
        warnings = vault_index.drain_parse_warnings()
        assert len(warnings) == 1
        assert "nested" in warnings[0]

    def test_update_index_non_string_tag_warning_recorded(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        notes_dir = tmp_vault / "Patterns"
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / "note.md").write_text(
            "---\ntags: [placeholder]\n---\n\n# Note\nBody.\n",
            encoding="utf-8",
        )
        # Simulate a legacy parser that coerced a list item to int.
        monkeypatch.setattr(
            update_index,
            "parse_frontmatter",
            lambda _content: {"tags": [2026, "python"]},
        )
        update_index.build_index(vault=tmp_vault)
        warnings = vault_index.drain_parse_warnings()
        assert any("coercing non-string tag" in w for w in warnings)


# ---------------------------------------------------------------------------
# slugify — non-ASCII safety
# ---------------------------------------------------------------------------


class TestSlugifyAsciiRegression:
    """Existing ASCII behavior must be unchanged (mirrors test_vault_common.py)."""

    def test_basic(self) -> None:
        assert vault_index.slugify("Hello World") == "hello-world"

    def test_underscores(self) -> None:
        assert vault_index.slugify("hello_world") == "hello-world"

    def test_special_characters(self) -> None:
        result = vault_index.slugify("Rust & WGPU: A Guide!")
        assert result == "rust-wgpu-a-guide"
        assert "--" not in result

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert vault_index.slugify("--hello--") == "hello"

    def test_multiple_hyphens_collapsed(self) -> None:
        assert vault_index.slugify("hello   world") == "hello-world"

    def test_empty_string(self) -> None:
        assert vault_index.slugify("") == ""

    def test_already_slugified(self) -> None:
        assert vault_index.slugify("already-slugified") == "already-slugified"

    def test_mixed_case(self) -> None:
        assert vault_index.slugify("CamelCase String") == "camelcase-string"

    def test_numbers_preserved(self) -> None:
        assert vault_index.slugify("wgpu 28 changes") == "wgpu-28-changes"

    def test_whitespace_stripped(self) -> None:
        assert vault_index.slugify("  padded  ") == "padded"


class TestSlugifyNonAscii:
    """Non-ASCII titles must transliterate or fall back to a stable hash."""

    def test_accented_transliterates(self) -> None:
        assert vault_index.slugify("Café Notes") == "cafe-notes"

    def test_accented_mixed(self) -> None:
        assert vault_index.slugify("Résumé Über Naïve") == "resume-uber-naive"

    def test_cjk_falls_back_to_stable_hash(self) -> None:
        result = vault_index.slugify("日本語")
        assert result.startswith("note-")
        assert len(result) == len("note-") + 8

    def test_cjk_fallback_is_stable(self) -> None:
        assert vault_index.slugify("日本語") == vault_index.slugify("日本語")

    def test_distinct_cjk_titles_get_different_slugs(self) -> None:
        slug_a = vault_index.slugify("日本語")
        slug_b = vault_index.slugify("中文标题")
        assert slug_a != slug_b
        assert slug_a.startswith("note-")
        assert slug_b.startswith("note-")


# ---------------------------------------------------------------------------
# Singleton guard — race-free PID claim
# ---------------------------------------------------------------------------


class TestSingletonGuardAtomicClaim:
    """Tests for update_index._write_pid / _singleton_guard."""

    @staticmethod
    def _patch_vault_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import vault_common as vc

        monkeypatch.setattr(vc, "VAULT_ROOT", tmp_path)
        monkeypatch.setattr(update_index, "VAULT_ROOT", tmp_path)

    def test_write_pid_atomic_create(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_vault_root(monkeypatch, tmp_path)
        update_index._write_pid()
        pf = update_index.pid_file()
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_write_pid_second_claim_raises_file_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_vault_root(monkeypatch, tmp_path)
        update_index._write_pid()
        with pytest.raises(FileExistsError):
            update_index._write_pid()

    def test_singleton_guard_fresh_claim_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_vault_root(monkeypatch, tmp_path)
        update_index._singleton_guard()
        pf = update_index.pid_file()
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_singleton_guard_stale_pid_recovers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_vault_root(monkeypatch, tmp_path)
        pf = update_index.pid_file()
        pf.write_text("99999999", encoding="utf-8")  # bogus PID, treated as dead below
        monkeypatch.setattr(update_index, "_is_process_running", lambda pid: False)
        update_index._singleton_guard()
        assert pf.read_text(encoding="utf-8").strip() == str(os.getpid())

    def test_singleton_guard_live_pid_bails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_vault_root(monkeypatch, tmp_path)
        real_pid = os.getpid()  # genuinely running -- this test process itself
        pf = update_index.pid_file()
        pf.write_text(str(real_pid), encoding="utf-8")
        # Simulate "our" PID being different so the guard treats the file's
        # PID as belonging to another (genuinely alive) process rather than
        # hitting the self-exclusion branch.
        monkeypatch.setattr(update_index.os, "getpid", lambda: real_pid + 1)
        with pytest.raises(SystemExit) as exc_info:
            update_index._singleton_guard()
        assert exc_info.value.code == 0
        # The other process's PID file must be left untouched.
        assert pf.read_text(encoding="utf-8").strip() == str(real_pid)
