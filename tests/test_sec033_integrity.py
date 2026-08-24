"""SEC-033: minor integrity fixes.

(a) vault_links preserves note mode across the tmp+replace write
(b) vault_conflicts locks the destination, not the tmp
(c) vault_merge escapes quotes in inline YAML lists
(d) doctor frontmatter AI repair keeps the original body
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills" / "parsidion/scripts")
)

import vault_common  # noqa: E402
import vault_conflicts  # noqa: E402
import vault_merge  # noqa: E402
from core.vault_links import inject_related_links  # noqa: E402
from doctor.frontmatter import splice_frontmatter_onto_original  # noqa: E402


@pytest.fixture(autouse=True)
def _vault(tmp_vault: Path) -> Path:
    return tmp_vault


class TestVaultLinksModePreserved:
    def test_restricted_note_mode_survives_backlink_write(
        self, tmp_vault: Path
    ) -> None:
        note = tmp_vault / "Patterns"
        note.mkdir(parents=True)
        target = note / "a.md"
        target.write_text(
            "---\ntype: pattern\nrelated: []\n---\n\n# A\n", encoding="utf-8"
        )
        target.chmod(0o600)
        before = target.stat().st_mode & 0o777

        inject_related_links(target, ["[[b]]"])

        assert target.stat().st_mode & 0o777 == before, (
            f"mode reset by backlink write: {oct(target.stat().st_mode & 0o777)}"
        )
        assert "[[b]]" in target.read_text(encoding="utf-8")


class TestConflictReportLock:
    def test_write_produces_valid_report_no_tmp(self, tmp_vault: Path) -> None:
        conflicts = [{"a": "note-a", "b": "note-b", "reason": "x", "score": 0.9}]
        vault_conflicts.write_conflict_report(conflicts, tmp_vault)
        report = tmp_vault / "conflicts" / "report.json"
        assert report.exists()
        assert json.loads(report.read_text(encoding="utf-8")) == conflicts
        assert not (tmp_vault / "conflicts" / "report.json.tmp").exists()


class TestMergeFrontmatterQuoteEscaping:
    def test_embedded_double_quote_is_escaped_and_round_trips(self) -> None:
        fm = vault_merge._build_frontmatter(
            {"tags": ['tag with "quotes"'], "related": [], "sources": []}
        )
        assert '\\"quotes\\"' in fm
        # Round-trip through the vault's frontmatter parser.
        parsed = vault_common.parse_frontmatter(fm)
        assert parsed.get("tags") == ['tag with "quotes"']

    def test_escaped_quote_does_not_split_the_list(self) -> None:
        fm = vault_merge._build_frontmatter(
            {"tags": ['a, comma "and" quote', "plain"], "related": [], "sources": []}
        )
        parsed = vault_common.parse_frontmatter(fm)
        assert parsed.get("tags") == ['a, comma "and" quote', "plain"]

    def test_backslash_round_trips(self) -> None:
        fm = vault_merge._build_frontmatter(
            {"tags": ["path\\to\\thing"], "related": [], "sources": []}
        )
        parsed = vault_common.parse_frontmatter(fm)
        assert parsed.get("tags") == ["path\\to\\thing"]


class TestDoctorSpliceKeepsOriginalBody:
    def test_ai_body_drift_is_discarded(self) -> None:
        original = (
            "---\n"
            "type: pattern\n"
            "related: []\n"
            "---\n"
            "\n"
            "# Original\n"
            "\n"
            "Body text with a code block:\n"
            "\n"
            "    indented code\n"
        )
        repaired = (
            "---\n"
            "type: pattern\n"
            'related: ["[[other]]"]\n'
            "---\n"
            "\n"
            "# Original (paraphrased by the model)\n"
            "\n"
            "Body text the model truncated.\n"
        )
        spliced = splice_frontmatter_onto_original(repaired, original)
        assert 'related: ["[[other]]"]' in spliced
        assert "indented code" in spliced, "original body must survive"
        assert "paraphrased" not in spliced, "AI body drift must be discarded"

    def test_no_frontmatter_original_uses_entire_original_as_body(self) -> None:
        original = "# Just a body\n\nno frontmatter yet\n"
        repaired = '---\ntype: pattern\nrelated: ["[[x]]"]\n---\n\n# Just a body\n'
        spliced = splice_frontmatter_onto_original(repaired, original)
        assert spliced.startswith("---\n")
        assert "no frontmatter yet" in spliced
        assert spliced.endswith(original)
