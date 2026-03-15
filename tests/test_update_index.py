"""Skeleton tests for update_index.py.

ARC-017: These tests cover the core utility functions that can be tested
without a live vault.  Full integration tests (index build, MANIFEST generation,
DB writes) require a populated vault and are left as future work.
"""

import sys
from pathlib import Path

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "skills" / "claude-vault" / "scripts"),
)

import update_index  # noqa: E402


class TestExtractTitle:
    """Tests for update_index._extract_title (delegates to vault_common.extract_title)."""

    def test_heading_extracted(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# My Note Title\nBody.\n"
        assert update_index._extract_title(content, "my-note-title") == "My Note Title"

    def test_fallback_to_stem(self) -> None:
        content = "---\ndate: 2026-01-01\n---\nNo heading here.\n"
        assert update_index._extract_title(content, "my-note-title") == "My Note Title"

    def test_double_hash_not_matched(self) -> None:
        """A ## heading should not be used as the note title."""
        content = "---\ndate: 2026-01-01\n---\n\n## Section Heading\nBody.\n"
        result = update_index._extract_title(content, "my-note")
        assert result == "My Note"


class TestExtractSummary:
    """Tests for update_index._extract_summary."""

    def test_first_body_line(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# Title\nThis is the summary.\n"
        assert update_index._extract_summary(content) == "This is the summary."

    def test_skips_headings(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n# Title\n## Section\nActual content.\n"
        assert update_index._extract_summary(content) == "Actual content."

    def test_skips_comments(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n<!-- comment -->\nReal line.\n"
        assert update_index._extract_summary(content) == "Real line."

    def test_truncation(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n" + "x" * 200 + "\n"
        result = update_index._extract_summary(content)
        assert len(result) <= update_index.SUMMARY_MAX_CHARS
        assert result.endswith("...")

    def test_empty_body(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n"
        assert update_index._extract_summary(content) == ""


class TestFolderName:
    """Tests for update_index._folder_name."""

    def test_direct_vault_child(self, tmp_path: Path) -> None:
        import vault_common as vc

        original_vc_root = vc.VAULT_ROOT
        original_ui_root = update_index.VAULT_ROOT
        # Both vault_common.VAULT_ROOT and update_index.VAULT_ROOT (the imported
        # binding) must be patched so _folder_name sees the right root.
        vc.VAULT_ROOT = tmp_path
        update_index.VAULT_ROOT = tmp_path
        try:
            note = tmp_path / "Patterns" / "my-note.md"
            result = update_index._folder_name(note)
            assert result == "Patterns"
        finally:
            vc.VAULT_ROOT = original_vc_root
            update_index.VAULT_ROOT = original_ui_root

    def test_root_level_note(self, tmp_path: Path) -> None:
        import vault_common as vc

        original_vc_root = vc.VAULT_ROOT
        original_ui_root = update_index.VAULT_ROOT
        vc.VAULT_ROOT = tmp_path
        update_index.VAULT_ROOT = tmp_path
        try:
            note = tmp_path / "CLAUDE.md"
            result = update_index._folder_name(note)
            assert result == ""
        finally:
            vc.VAULT_ROOT = original_vc_root
            update_index.VAULT_ROOT = original_ui_root


class TestExtractWikilinkStems:
    """Tests for update_index._extract_wikilink_stems."""

    def test_inline_wikilinks(self) -> None:
        related = ["[[note-a]]", "[[note-b]]"]
        stems = update_index._extract_wikilink_stems(related)
        assert stems == ["note-a", "note-b"]

    def test_bare_strings(self) -> None:
        stems = update_index._extract_wikilink_stems(["bare-string"])
        assert stems == ["bare-string"]

    def test_non_list(self) -> None:
        assert update_index._extract_wikilink_stems("not-a-list") == []  # type: ignore[arg-type]

    def test_empty_list(self) -> None:
        assert update_index._extract_wikilink_stems([]) == []

    def test_mixed(self) -> None:
        related = ["[[note-a]]", "bare", 42]  # type: ignore[list-item]
        stems = update_index._extract_wikilink_stems(related)
        assert "note-a" in stems
        assert "bare" in stems
