"""Tests for code-fence-aware wikilink rewriting.

Covers ``vault_links.replace_wikilinks_outside_code`` /
``vault_links.sub_wikilinks_outside_code`` directly, plus one end-to-end
regression per adopting call site (``vault_doctor.run_strip_prefixes`` and
``vault_merge._update_wikilinks_in_vault``) to prove wikilink examples
inside fenced code blocks survive a real rename/merge without corruption.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402
import vault_doctor  # noqa: E402
import vault_links  # noqa: E402
import vault_merge  # noqa: E402


@pytest.fixture()
def vault(tmp_vault: Path) -> Path:
    """Create standard vault directories and return vault root."""
    for d in vault_common.VAULT_DIRS:
        (tmp_vault / d).mkdir(exist_ok=True)
    return tmp_vault


def _note(vault: Path, rel_path: str, content: str) -> Path:
    p = vault / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# replace_wikilinks_outside_code
# ---------------------------------------------------------------------------


class TestReplaceWikilinksOutsideCode:
    def test_rewrites_prose_link(self) -> None:
        content = "See [[old-note]] for details.\n"
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert out == "See [[new-note]] for details.\n"

    def test_rewrites_alias_form(self) -> None:
        content = "[[old-note|Display Text]] is useful.\n"
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert out == "[[new-note|Display Text]] is useful.\n"

    def test_no_rewrite_inside_fenced_block_with_language_tag(self) -> None:
        content = (
            "Prose [[old-note]] link.\n"
            "\n"
            "```python\n"
            "# Example: [[old-note]] is a wikilink\n"
            "```\n"
        )
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert "Prose [[new-note]] link." in out
        assert "# Example: [[old-note]] is a wikilink" in out
        assert "[[new-note]] is a wikilink" not in out

    def test_no_rewrite_inside_tilde_fence(self) -> None:
        content = "~~~\n[[old-note]] inside tilde fence\n~~~\n"
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert out == content

    def test_no_rewrite_inside_inline_code_span(self) -> None:
        content = "Use `[[old-note]]` syntax to link notes.\n"
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert out == content

    def test_frontmatter_related_still_rewritten(self) -> None:
        content = '---\ndate: 2026-07-01\nrelated: ["[[old-note]]"]\n---\n\n# Title\n'
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert 'related: ["[[new-note]]"]' in out

    def test_same_line_mix_of_code_span_and_prose_link(self) -> None:
        content = "Run `[[old-note]]` literally, but also see [[old-note]] in prose.\n"
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert "`[[old-note]]`" in out  # inline code span untouched
        assert "also see [[new-note]] in prose" in out

    def test_unclosed_fence_protects_rest_of_document(self) -> None:
        content = (
            "Prose [[old-note]] before the fence.\n"
            "\n"
            "```\n"
            "unterminated code block\n"
            "[[old-note]] never rewritten because the fence never closes\n"
        )
        out = vault_links.replace_wikilinks_outside_code(
            content, {"old-note": "new-note"}
        )
        assert "Prose [[new-note]] before the fence." in out
        assert "[[old-note]] never rewritten" in out

    def test_empty_replacements_returns_content_unchanged(self) -> None:
        content = "See [[old-note]].\n"
        assert vault_links.replace_wikilinks_outside_code(content, {}) == content


class TestStripUnresolvedWikilinks:
    def test_drops_non_resolving_related_and_body_links(self, vault: Path) -> None:
        _note(
            vault,
            "Patterns/real.md",
            "---\ndate: 2026-01-01\ntype: pattern\n---\n\n# Real\n",
        )
        content = (
            "---\ndate: 2026-07-01\ntype: pattern\n"
            'related: ["[[real]]", "[[par-bobble]]"]\n'
            "---\n\n# Title\n\nSee [[real]] and [[par-bobble]].\n"
        )
        out, removed = vault_links.strip_unresolved_wikilinks(content, vault)
        assert removed == 2  # [[par-bobble]] in related + body
        assert "[[real]]" in out
        assert "[[par-bobble]]" not in out
        assert "par-bobble" in out  # body link reduced to display text
        assert 'related: ["[[real]]"]' in out  # related keeps only resolving entry

    def test_preserves_links_inside_code_blocks(self, vault: Path) -> None:
        content = "```toml\n[[licenses.exceptions]]\n```\nSee `[[inline-code]]` too.\n"
        out, removed = vault_links.strip_unresolved_wikilinks(content, vault)
        assert removed == 0
        assert "[[licenses.exceptions]]" in out
        assert "[[inline-code]]" in out


# ---------------------------------------------------------------------------
# sub_wikilinks_outside_code (general primitive used by vault_merge)
# ---------------------------------------------------------------------------


class TestSubWikilinksOutsideCode:
    def test_pattern_and_callable_repl_skip_fenced_block(self) -> None:
        content = "[[old-note]] in prose.\n\n```\n[[old-note]] in code.\n```\n"
        pattern = re.compile(r"\[\[old-note\]\]")
        new_content, n = vault_links.sub_wikilinks_outside_code(
            content,
            pattern,
            lambda m: "[[new-note]]",  # noqa: ARG005
        )
        assert n == 1
        assert "[[new-note]] in prose." in new_content
        assert "[[old-note]] in code." in new_content


# ---------------------------------------------------------------------------
# End-to-end: vault_doctor.run_strip_prefixes
# ---------------------------------------------------------------------------


class TestStripPrefixesFenceAware:
    def test_fenced_example_survives_prefix_strip(self, vault: Path) -> None:
        _note(vault, "Projects/myapp/myapp-overview.md", "# Overview\n")
        linker = _note(
            vault,
            "Patterns/linker.md",
            (
                "# Linker\n\n"
                "See [[myapp-overview]] for details.\n\n"
                "Example wikilink syntax:\n"
                "```\n"
                "[[myapp-overview]] — do not rewrite this, it's documentation\n"
                "```\n"
            ),
        )

        vault_doctor.run_strip_prefixes(
            dry_run=False, vault_path=vault, auto_reindex=False
        )

        assert (vault / "Projects" / "myapp" / "overview.md").exists()
        body = linker.read_text(encoding="utf-8")
        assert "See [[overview]] for details." in body
        assert "[[myapp-overview]] — do not rewrite this" in body


# ---------------------------------------------------------------------------
# End-to-end: vault_merge._update_wikilinks_in_vault
# ---------------------------------------------------------------------------


class TestUpdateWikilinksInVaultFenceAware:
    def test_fenced_example_survives_merge_rewrite(self, vault: Path) -> None:
        note = _note(
            vault,
            "Patterns/other-note.md",
            (
                "See [[old-note]] for details.\n\n"
                "```\n"
                "[[old-note]] is example wikilink syntax\n"
                "```\n"
            ),
        )

        updated = vault_merge._update_wikilinks_in_vault("old-note", "new-note", vault)

        assert updated == 1
        content = note.read_text(encoding="utf-8")
        assert "See [[new-note]] for details." in content
        assert "[[old-note]] is example wikilink syntax" in content
