"""SEC-115 tests — vault-merge inlines note bodies instead of asking the
child agent to ``Read`` them.

The old ``_ai_merge_bodies`` prompt told a tool-enabled ``claude -p`` child
to "Read both files", giving the child filesystem access over content that
is itself AI-generated from transcripts — the only place in the repo that
hands filesystem access to a child agent over untrusted content. The fix
inlines both bodies in ``<note_a>`` / ``<note_b>`` delimiters with a
SYSTEM untrusted-data preamble (matching ``vault_conflicts.py``).

Also pins the strengthened output guard, which now rejects bodies wrapped
in code fences, YAML frontmatter, or opening refusal phrases.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_merge  # noqa: E402


class TestMergePromptInlinesBodies:
    """``_ai_merge_bodies`` prompt contains both bodies and no Read instruction."""

    def test_prompt_contains_both_bodies_and_no_read_instruction(
        self, tmp_path: Path
    ) -> None:
        note_a = tmp_path / "a.md"
        note_a.write_text(
            '---\ndate: 2026-01-01\ntype: pattern\ntags: [foo]\nrelated: ["[[b]]"]\n'
            "---\n# Note A\n\nApple details.\n",
            encoding="utf-8",
        )
        note_b = tmp_path / "b.md"
        note_b.write_text(
            '---\ndate: 2026-01-01\ntype: pattern\ntags: [foo]\nrelated: ["[[a]]"]\n'
            "---\n# Note B\n\nBanana details.\n",
            encoding="utf-8",
        )

        captured: dict[str, str] = {}

        def fake_run(prompt, **kwargs):
            captured["prompt"] = prompt
            return (
                "# Merged\n\n"
                "## Summary\n\nA combined note that is long enough to pass "
                "the validity check easily."
            )

        with patch.object(
            vault_merge.ai_backend, "run_ai_prompt", side_effect=fake_run
        ):
            result = vault_merge._ai_merge_bodies(note_a, note_b, title="Fruit")
        assert result is not None
        p = captured["prompt"]
        # Bodies are inlined inside the delimiters.
        assert "<note_a>" in p and "</note_a>" in p
        assert "<note_b>" in p and "</note_b>" in p
        assert "Apple details" in p
        assert "Banana details" in p
        # The "Read both files" instruction has been removed.
        assert "Read both files" not in p
        # The untrusted-data SYSTEM preamble is present.
        assert "UNTRUSTED DATA" in p
        assert "SYSTEM:" in p

    def test_bodies_are_read_via_get_body_not_raw(self, tmp_path: Path) -> None:
        """Inline bodies should be the body part (frontmatter stripped), not
        the raw note content — otherwise the prompt leaks ``related`` etc.
        into the model's view as if they were fact.
        """
        note_a = tmp_path / "a.md"
        note_a.write_text(
            '---\ndate: 2026-01-01\ntype: pattern\ntags: [foo]\nrelated: ["[[b]]"]\n'
            "---\n# Note A\n\nApple.\n",
            encoding="utf-8",
        )
        note_b = tmp_path / "b.md"
        note_b.write_text(
            '---\ndate: 2026-01-01\ntype: pattern\ntags: [foo]\nrelated: ["[[a]]"]\n'
            "---\n# Note B\n\nBanana.\n",
            encoding="utf-8",
        )

        captured: dict[str, str] = {}

        def fake_run(prompt, **kwargs):
            captured["prompt"] = prompt
            return (
                "# Merged\n\n"
                "## Summary\n\nA combined note that is long enough to pass "
                "the validity check easily."
            )

        with patch.object(
            vault_merge.ai_backend, "run_ai_prompt", side_effect=fake_run
        ):
            vault_merge._ai_merge_bodies(note_a, note_b, title="Fruit")

        # Frontmatter should NOT appear inside the inlined bodies.
        assert "related: [[b]]" not in captured["prompt"]
        assert "type: pattern" not in captured["prompt"]


class TestIsValidMergeBodyStrengthened:
    """The strengthened output guard rejects refusals, fences, and frontmatter."""

    def test_accepts_normal_heading_body(self) -> None:
        body = "# Real Note\n\nThis is a long enough merged body that passes.\n"
        assert vault_merge._is_valid_merge_body(body) is True

    def test_rejects_short_output(self) -> None:
        assert vault_merge._is_valid_merge_body("# short") is False

    def test_rejects_frontmatter_wrapper(self) -> None:
        body = (
            "---\ndate: 2026-01-01\ntype: pattern\n---\n"
            "# Real Body\n\nlong enough body to pass length check."
        )
        assert vault_merge._is_valid_merge_body(body) is False

    def test_rejects_code_fence_wrapper(self) -> None:
        body = "```markdown\n# Real Body\n\nlong enough body to pass length check.\n```"
        assert vault_merge._is_valid_merge_body(body) is False

    def test_rejects_heading_prefixed_refusal(self) -> None:
        body = (
            "# Unable to merge\n\n"
            "I cannot merge these notes because they are too different."
        )
        assert vault_merge._is_valid_merge_body(body) is False

    def test_rejects_sorry_refusal(self) -> None:
        body = (
            "# Sorry, I can't help with that.\n\n"
            "The request asks me to combine notes that contain sensitive data."
        )
        assert vault_merge._is_valid_merge_body(body) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
