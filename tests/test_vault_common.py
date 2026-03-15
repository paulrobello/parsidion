"""Unit tests for vault_common.py core functions.

Tests cover: parse_frontmatter, _parse_config_yaml, slugify,
extract_text_from_content, read_last_n_lines, and flock_exclusive/funlock.

These tests use only stdlib + pytest and do not require a live vault.
"""

import sys
from pathlib import Path

# Mirror the sys.path.insert pattern used by all scripts so we can import
# vault_common without pip install.  See ARC-009.
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "skills" / "claude-vault" / "scripts"),
)

import vault_common  # noqa: E402


# ---------------------------------------------------------------------------
# parse_frontmatter
# ---------------------------------------------------------------------------


class TestParseFrontmatter:
    """Tests for vault_common.parse_frontmatter."""

    def test_empty_string(self) -> None:
        assert vault_common.parse_frontmatter("") == {}

    def test_no_frontmatter(self) -> None:
        assert vault_common.parse_frontmatter("# Just a heading\nBody text.") == {}

    def test_basic_scalars(self) -> None:
        content = (
            "---\ndate: 2026-01-15\ntype: research\nconfidence: high\n---\nBody.\n"
        )
        result = vault_common.parse_frontmatter(content)
        assert result["date"] == "2026-01-15"
        assert result["type"] == "research"
        assert result["confidence"] == "high"

    def test_inline_list(self) -> None:
        content = "---\ntags: [python, rust, ai]\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["tags"] == ["python", "rust", "ai"]

    def test_empty_inline_list(self) -> None:
        content = "---\nsources: []\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["sources"] == []

    def test_multiline_list(self) -> None:
        content = "---\ntags:\n  - alpha\n  - beta\n  - gamma\n---\nBody.\n"
        result = vault_common.parse_frontmatter(content)
        assert result["tags"] == ["alpha", "beta", "gamma"]

    def test_boolean_values(self) -> None:
        content = (
            "---\nenabled: true\ndisabled: false\nalso_yes: yes\nalso_no: no\n---\n"
        )
        result = vault_common.parse_frontmatter(content)
        assert result["enabled"] is True
        assert result["disabled"] is False
        assert result["also_yes"] is True
        assert result["also_no"] is False

    def test_integer_value(self) -> None:
        content = "---\ncount: 42\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_float_value(self) -> None:
        content = "---\nratio: 3.14\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["ratio"] == 3.14
        assert isinstance(result["ratio"], float)

    def test_null_value(self) -> None:
        content = "---\nfield: null\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["field"] is None

    def test_quoted_string(self) -> None:
        content = '---\ntitle: "Hello World"\n---\n'
        result = vault_common.parse_frontmatter(content)
        assert result["title"] == "Hello World"

    def test_single_quoted_string(self) -> None:
        content = "---\ntitle: 'Hello World'\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["title"] == "Hello World"

    def test_wikilink_list(self) -> None:
        content = '---\nrelated: ["[[Topic One]]", "[[Topic Two]]"]\n---\n'
        result = vault_common.parse_frontmatter(content)
        assert result["related"] == ["[[Topic One]]", "[[Topic Two]]"]

    def test_comment_lines_skipped(self) -> None:
        content = "---\n# This is a comment\ndate: 2026-01-15\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert "comment" not in str(result)
        assert result["date"] == "2026-01-15"

    def test_missing_closing_fence(self) -> None:
        content = "---\ndate: 2026-01-15\n"
        result = vault_common.parse_frontmatter(content)
        assert result == {}

    def test_full_realistic_note(self) -> None:
        content = (
            "---\n"
            "date: 2026-03-10\n"
            "type: debugging\n"
            "tags: [rust, wgpu, rendering]\n"
            "project: par-voxel\n"
            "confidence: high\n"
            "sources: []\n"
            'related: ["[[WGPU Basics]]"]\n'
            "session_id: abc123\n"
            "---\n"
            "\n"
            "## WGPU Pipeline Fix\n"
            "\nBody content here.\n"
        )
        result = vault_common.parse_frontmatter(content)
        assert result["date"] == "2026-03-10"
        assert result["type"] == "debugging"
        assert result["tags"] == ["rust", "wgpu", "rendering"]
        assert result["project"] == "par-voxel"
        assert result["confidence"] == "high"
        assert result["sources"] == []
        assert result["related"] == ["[[WGPU Basics]]"]
        assert result["session_id"] == "abc123"


# ---------------------------------------------------------------------------
# _parse_config_yaml
# ---------------------------------------------------------------------------


class TestParseConfigYaml:
    """Tests for vault_common._parse_config_yaml."""

    def test_empty_string(self) -> None:
        assert vault_common._parse_config_yaml("") == {}

    def test_top_level_scalars(self) -> None:
        text = "timeout: 30\nmodel: claude-haiku\nenabled: true\n"
        result = vault_common._parse_config_yaml(text)
        assert result["timeout"] == 30
        assert result["model"] == "claude-haiku"
        assert result["enabled"] is True

    def test_nested_section(self) -> None:
        text = (
            "session_start_hook:\n"
            "  ai_model: claude-haiku\n"
            "  max_chars: 4000\n"
            "  debug: false\n"
        )
        result = vault_common._parse_config_yaml(text)
        section = result["session_start_hook"]
        assert isinstance(section, dict)
        assert section["ai_model"] == "claude-haiku"
        assert section["max_chars"] == 4000
        assert section["debug"] is False

    def test_multiple_sections(self) -> None:
        text = "section_a:\n  key1: value1\nsection_b:\n  key2: value2\n"
        result = vault_common._parse_config_yaml(text)
        assert result["section_a"]["key1"] == "value1"
        assert result["section_b"]["key2"] == "value2"

    def test_inline_comment_stripped(self) -> None:
        text = "timeout: 30  # seconds\n"
        result = vault_common._parse_config_yaml(text)
        assert result["timeout"] == 30

    def test_comment_lines_skipped(self) -> None:
        text = "# This is a comment\ntimeout: 30\n"
        result = vault_common._parse_config_yaml(text)
        assert result["timeout"] == 30
        assert len(result) == 1

    def test_blank_lines_skipped(self) -> None:
        text = "key1: value1\n\nkey2: value2\n"
        result = vault_common._parse_config_yaml(text)
        assert result["key1"] == "value1"
        assert result["key2"] == "value2"

    def test_unparsable_line_warns(self, capsys: object) -> None:
        """Lines without colons should produce a warning on stderr."""
        import io
        import contextlib

        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            result = vault_common._parse_config_yaml("no colon here\nkey: value\n")

        assert result["key"] == "value"
        assert "ignoring unparsable config line" in stderr_capture.getvalue()

    def test_indented_line_outside_section_warns(self) -> None:
        """An indented key-value without a preceding section header should warn."""
        import io
        import contextlib

        stderr_capture = io.StringIO()
        with contextlib.redirect_stderr(stderr_capture):
            result = vault_common._parse_config_yaml("  orphan_key: orphan_value\n")

        # The orphan key should not appear in the result
        assert "orphan_key" not in result
        assert (
            "ignoring indented config line outside any section"
            in stderr_capture.getvalue()
        )

    def test_mixed_scalars_and_sections(self) -> None:
        text = (
            "top_key: top_value\n"
            "section:\n"
            "  nested_key: nested_value\n"
            "another_top: 42\n"
        )
        result = vault_common._parse_config_yaml(text)
        assert result["top_key"] == "top_value"
        assert result["section"]["nested_key"] == "nested_value"
        assert result["another_top"] == 42


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    """Tests for vault_common.slugify."""

    def test_basic(self) -> None:
        assert vault_common.slugify("Hello World") == "hello-world"

    def test_underscores(self) -> None:
        assert vault_common.slugify("hello_world") == "hello-world"

    def test_special_characters(self) -> None:
        # Special chars are removed, then multiple hyphens are collapsed
        result = vault_common.slugify("Rust & WGPU: A Guide!")
        assert result == "rust-wgpu-a-guide"
        assert "--" not in result

    def test_leading_trailing_hyphens_stripped(self) -> None:
        assert vault_common.slugify("--hello--") == "hello"

    def test_multiple_hyphens_collapsed(self) -> None:
        assert vault_common.slugify("hello   world") == "hello-world"

    def test_empty_string(self) -> None:
        assert vault_common.slugify("") == ""

    def test_already_slugified(self) -> None:
        assert vault_common.slugify("already-slugified") == "already-slugified"

    def test_mixed_case(self) -> None:
        assert vault_common.slugify("CamelCase String") == "camelcase-string"

    def test_numbers_preserved(self) -> None:
        assert vault_common.slugify("wgpu 28 changes") == "wgpu-28-changes"

    def test_whitespace_stripped(self) -> None:
        assert vault_common.slugify("  padded  ") == "padded"


# ---------------------------------------------------------------------------
# extract_text_from_content
# ---------------------------------------------------------------------------


class TestExtractTextFromContent:
    """Tests for vault_common.extract_text_from_content."""

    def test_string_passthrough(self) -> None:
        assert vault_common.extract_text_from_content("hello") == "hello"

    def test_empty_string(self) -> None:
        assert vault_common.extract_text_from_content("") == ""

    def test_text_blocks(self) -> None:
        blocks: list[dict] = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        result = vault_common.extract_text_from_content(blocks)
        assert result == "first\nsecond"

    def test_non_text_blocks_skipped(self) -> None:
        blocks: list[dict] = [
            {"type": "tool_use", "name": "Read"},
            {"type": "text", "text": "visible"},
            {"type": "tool_result", "content": "ignored"},
        ]
        result = vault_common.extract_text_from_content(blocks)
        assert result == "visible"

    def test_empty_list(self) -> None:
        assert vault_common.extract_text_from_content([]) == ""

    def test_non_string_text_skipped(self) -> None:
        blocks: list[dict] = [
            {"type": "text", "text": 42},  # type: ignore[dict-item]
        ]
        result = vault_common.extract_text_from_content(blocks)
        assert result == ""

    def test_non_list_non_string_returns_empty(self) -> None:
        assert vault_common.extract_text_from_content(42) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# read_last_n_lines
# ---------------------------------------------------------------------------


class TestReadLastNLines:
    """Tests for vault_common.read_last_n_lines."""

    def test_basic(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
        result = vault_common.read_last_n_lines(f, 3)
        assert len(result) == 3
        assert result[0].strip() == "line3"
        assert result[1].strip() == "line4"
        assert result[2].strip() == "line5"

    def test_fewer_lines_than_requested(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\n", encoding="utf-8")
        result = vault_common.read_last_n_lines(f, 10)
        assert len(result) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("", encoding="utf-8")
        result = vault_common.read_last_n_lines(f, 5)
        assert result == []

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        f = tmp_path / "nonexistent.txt"
        result = vault_common.read_last_n_lines(f, 5)
        assert result == []

    def test_single_line(self, tmp_path: Path) -> None:
        f = tmp_path / "test.txt"
        f.write_text("only line\n", encoding="utf-8")
        result = vault_common.read_last_n_lines(f, 1)
        assert len(result) == 1
        assert result[0].strip() == "only line"

    def test_large_file_memory_efficient(self, tmp_path: Path) -> None:
        """Verify that reading tail of a large file works correctly.

        This test ensures the deque-based implementation returns the
        correct last N lines regardless of file size.
        """
        f = tmp_path / "large.txt"
        lines = [f"line-{i}\n" for i in range(10000)]
        f.write_text("".join(lines), encoding="utf-8")
        result = vault_common.read_last_n_lines(f, 5)
        assert len(result) == 5
        assert result[0].strip() == "line-9995"
        assert result[4].strip() == "line-9999"

    def test_binary_content_handled(self, tmp_path: Path) -> None:
        """Files with non-UTF-8 content should not raise (errors='replace')."""
        f = tmp_path / "binary.txt"
        f.write_bytes(b"good line\n\xff\xfe bad bytes\n")
        result = vault_common.read_last_n_lines(f, 5)
        assert len(result) >= 1  # Should not raise


# ---------------------------------------------------------------------------
# flock_exclusive / funlock
# ---------------------------------------------------------------------------


class TestFileLocking:
    """Basic tests for flock_exclusive and funlock."""

    def test_lock_unlock_cycle(self, tmp_path: Path) -> None:
        """Verify that locking and unlocking a file does not raise."""
        f = tmp_path / "lockfile.txt"
        f.write_text("data", encoding="utf-8")
        with open(f, encoding="utf-8") as fh:
            vault_common.flock_exclusive(fh)
            vault_common.funlock(fh)

    def test_shared_lock(self, tmp_path: Path) -> None:
        """Verify that shared lock and unlock does not raise."""
        f = tmp_path / "lockfile.txt"
        f.write_text("data", encoding="utf-8")
        with open(f, encoding="utf-8") as fh:
            vault_common.flock_shared(fh)
            vault_common.funlock(fh)

    def test_multiple_shared_locks(self, tmp_path: Path) -> None:
        """Multiple shared locks on the same file should not deadlock."""
        f = tmp_path / "lockfile.txt"
        f.write_text("data", encoding="utf-8")
        with (
            open(f, encoding="utf-8") as fh1,
            open(f, encoding="utf-8") as fh2,
        ):
            vault_common.flock_shared(fh1)
            vault_common.flock_shared(fh2)
            vault_common.funlock(fh1)
            vault_common.funlock(fh2)


# ---------------------------------------------------------------------------
# validate_vault_path (from install.py)
# ---------------------------------------------------------------------------


class TestValidateVaultPath:
    """Tests for install.validate_vault_path."""

    @staticmethod
    def _import_install():
        """Import install.py from the repo root."""
        repo_root = Path(__file__).resolve().parent.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        import install  # noqa: E402

        return install

    def test_valid_path(self) -> None:
        install = self._import_install()
        # Use a home-relative path that is not under any forbidden system directory.
        # pytest's tmp_path lives in /private/var/... on macOS which correctly
        # matches the forbidden /var prefix — so we use ~/ClaudeVaultTestPath instead.
        test_vault = str(Path.home() / "ClaudeVaultTestPath" / "MyVault")
        path, error = install.validate_vault_path(test_vault)
        assert error is None
        assert path.name == "MyVault"

    def test_empty_path(self) -> None:
        install = self._import_install()
        path, error = install.validate_vault_path("")
        assert error is not None
        assert "empty" in error.lower()

    def test_whitespace_only_path(self) -> None:
        install = self._import_install()
        path, error = install.validate_vault_path("   ")
        assert error is not None
        assert "empty" in error.lower()

    def test_forbidden_system_path(self) -> None:
        install = self._import_install()
        path, error = install.validate_vault_path("/usr/local/vault")
        assert error is not None
        assert "system" in error.lower() or "cannot" in error.lower()

    def test_home_tilde_expansion(self) -> None:
        install = self._import_install()
        path, error = install.validate_vault_path("~/MyVault")
        assert error is None
        assert str(Path.home()) in str(path)

    def test_claude_dir_forbidden(self) -> None:
        install = self._import_install()
        claude_path = str(Path.home() / ".claude" / "vault")
        path, error = install.validate_vault_path(claude_path)
        assert error is not None


# ---------------------------------------------------------------------------
# parse_frontmatter — multiline scalars (ARC-007)
# ---------------------------------------------------------------------------


class TestParseFrontmatterMultilineScalars:
    """Tests for multiline scalar support in vault_common.parse_frontmatter.

    The parser supports four block scalar indicators:
      ``|``  — literal block: lines joined with newlines
      ``|-`` — literal block strip: same, trailing blank lines stripped
      ``>``  — folded block: lines joined with a single space
      ``>-`` — folded block strip: same, trailing blank lines stripped

    Only indented continuation lines (indent > 0) are collected.
    The block ends at the next bare key or blank line.
    """

    def test_literal_block_scalar(self) -> None:
        """``|`` joins continuation lines with newlines."""
        content = (
            "---\nsummary: |\n  First line.\n  Second line.\ndate: 2026-01-01\n---\n"
        )
        result = vault_common.parse_frontmatter(content)
        assert result["summary"] == "First line.\nSecond line."
        assert result["date"] == "2026-01-01"

    def test_folded_block_scalar(self) -> None:
        """``>`` joins continuation lines with spaces."""
        content = (
            "---\nsummary: >\n  First line.\n  Second line.\ndate: 2026-01-01\n---\n"
        )
        result = vault_common.parse_frontmatter(content)
        assert result["summary"] == "First line. Second line."
        assert result["date"] == "2026-01-01"

    def test_literal_strip_variant(self) -> None:
        """``|-`` is the strip variant of the literal block."""
        content = "---\nnote: |-\n  Line one.\n  Line two.\nafter: done\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["note"] == "Line one.\nLine two."

    def test_folded_strip_variant(self) -> None:
        """``>-`` is the strip variant of the folded block."""
        content = "---\nnote: >-\n  Line one.\n  Line two.\nafter: done\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["note"] == "Line one. Line two."

    def test_block_ends_at_blank_line(self) -> None:
        """A blank line terminates a multiline scalar block."""
        content = "---\nsummary: |\n  Only this line.\n\ndate: 2026-01-01\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["summary"] == "Only this line."
        assert result["date"] == "2026-01-01"

    def test_block_ends_at_eof(self) -> None:
        """A multiline scalar that ends at the closing --- fence is flushed."""
        content = "---\nsummary: |\n  Flushed at end.\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["summary"] == "Flushed at end."

    def test_single_continuation_line(self) -> None:
        """A block with a single indented line works correctly."""
        content = "---\nnote: >\n  Single line.\n---\n"
        result = vault_common.parse_frontmatter(content)
        assert result["note"] == "Single line."


# ---------------------------------------------------------------------------
# get_body
# ---------------------------------------------------------------------------


class TestGetBody:
    """Tests for vault_common.get_body."""

    def test_with_frontmatter(self) -> None:
        content = "---\ndate: 2026-01-01\n---\n\n## Title\nBody text."
        body = vault_common.get_body(content)
        # get_body returns everything after the closing "---\n"
        assert "## Title" in body
        assert "Body text." in body
        assert "date:" not in body

    def test_without_frontmatter(self) -> None:
        content = "# Just a heading\nBody text."
        body = vault_common.get_body(content)
        assert body == content
