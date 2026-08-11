"""Broken-wikilink repair must never substitute a Daily journal note.

``_find_link_replacement``'s last resort is a semantic ``vault-search`` on the
broken link text.  Daily notes name every project worked that day, so a link
like ``[[par-rt-db]]`` scores highest against a daily journal page — and the
repair's success test is only "does it resolve".  Observed 2026-08-10: all four
repairs in one ``--fix-all`` run rewrote real targets to ``[[10-probello]]``,
``[[30-probello]]``, ``[[29-probello]]``, ``[[08-probello]]``.

A dropped link is recoverable (the backlink pass refills ``related``); a wrong
link is not, because the re-scan then reports the note clean.  So the semantic
fallback skips Daily notes, and an *explicit* link to a daily note still
resolves through the exact-match path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "skills/parsidion/scripts")
)

import vault_doctor  # noqa: E402
from doctor import frontmatter as doctor_frontmatter  # noqa: E402


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "Patterns").mkdir(parents=True)
    (tmp_path / "Daily" / "2026-08").mkdir(parents=True)
    return tmp_path


def _write(
    vault: Path, rel: str, content: str = "---\ntype: pattern\n---\n\n# X\n"
) -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def fake_search(monkeypatch: pytest.MonkeyPatch):
    """Stub `vault-search` with a caller-supplied hit list."""

    def _install(hits: list[Path]) -> None:
        payload = json.dumps(
            [{"path": str(p), "stem": p.stem, "score": 0.9} for p in hits]
        )

        class _Completed:
            returncode = 0
            stdout = payload

        def fake_run(argv: list[str], **_kwargs: object) -> object:
            return _Completed()

        monkeypatch.setattr(vault_doctor.subprocess, "run", fake_run)

    return _install


class TestFindLinkReplacement:
    def test_daily_hit_is_skipped_for_the_next_real_note(
        self, vault: Path, fake_search
    ) -> None:
        daily = _write(vault, "Daily/2026-08/10-probello.md")
        real = _write(vault, "Patterns/config-clamp-pattern.md")
        fake_search([daily, real])

        result = vault_doctor._find_link_replacement("par-rt-db", {}, exclude_path=None)

        assert result == "config-clamp-pattern"

    def test_returns_none_when_every_hit_is_a_daily_note(
        self, vault: Path, fake_search
    ) -> None:
        """Dropping the link beats substituting a journal page."""
        fake_search(
            [
                _write(vault, "Daily/2026-08/10-probello.md"),
                _write(vault, "Daily/2026-08/09-probello.md"),
            ]
        )

        result = vault_doctor._find_link_replacement(
            "fix-audit-remediation", {}, exclude_path=None
        )

        assert result is None

    def test_explicit_exact_link_to_a_daily_note_still_resolves(
        self, vault: Path
    ) -> None:
        """The exclusion targets the semantic guess, not a deliberate link."""
        daily = _write(vault, "Daily/2026-08/10-probello.md")
        note_map = vault_doctor.build_note_map([daily])

        result = vault_doctor._find_link_replacement(
            "10-probello", note_map, exclude_path=None
        )

        assert result == "10-probello"


class TestFindSemanticCandidates:
    def test_daily_notes_are_not_offered_as_candidates(
        self, vault: Path, fake_search
    ) -> None:
        target = _write(
            vault, "Patterns/subject.md", "---\ntype: pattern\n---\n\n# Subject\n"
        )
        daily = _write(vault, "Daily/2026-08/10-probello.md")
        real = _write(vault, "Patterns/genuinely-related.md")
        fake_search([daily, real])

        candidates = vault_doctor._find_semantic_candidates(target)

        assert candidates == ["genuinely-related"]


class TestRepairPrompt:
    def _capture(self, vault: Path, monkeypatch: pytest.MonkeyPatch, code: str) -> str:
        note = _write(
            vault,
            "Patterns/subject.md",
            "---\n"
            "date: 2026-08-10\n"
            "type: pattern\n"
            'related: ["[[par-rt-db]]"]\n'
            "---\n\n# Subject\n",
        )
        seen: list[str] = []

        def fake_run_ai_prompt(prompt: str, **_kwargs: object) -> str:
            seen.append(prompt)
            return note.read_text(encoding="utf-8")

        monkeypatch.setattr(
            doctor_frontmatter.ai_backend, "run_ai_prompt", fake_run_ai_prompt
        )
        issues = [
            vault_doctor.Issue(note, "warning", code, "[[par-rt-db]] does not resolve")
        ]
        vault_doctor.repair_note(note, issues, vault_path=vault)
        assert seen, "the AI backend was never called"
        return seen[0]

    def test_prompt_forbids_daily_targets_and_prefers_dropping(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompt = self._capture(vault, monkeypatch, "BROKEN_WIKILINK")
        assert "NEVER link a daily note" in prompt
        assert "DROP it rather than substituting" in prompt

    def test_broken_wikilink_gets_a_candidate_list(
        self, vault: Path, monkeypatch: pytest.MonkeyPatch, fake_search
    ) -> None:
        """A BROKEN_WIKILINK-only note used to get an EMPTY candidate list while
        still being told every link must resolve — so the model invented one."""
        fake_search([_write(vault, "Patterns/genuinely-related.md")])
        prompt = self._capture(vault, monkeypatch, "BROKEN_WIKILINK")
        assert "[[genuinely-related]]" in prompt


class TestAutoRepairEndToEnd:
    def test_link_is_dropped_rather_than_pointed_at_a_daily_note(
        self, vault: Path, fake_search
    ) -> None:
        fake_search([_write(vault, "Daily/2026-08/10-probello.md")])
        note = _write(
            vault,
            "Patterns/subject.md",
            "---\n"
            "date: 2026-08-10\n"
            "type: pattern\n"
            'related: ["[[par-rt-db]]", "[[keeper]]"]\n'
            "---\n\n# Subject\n",
        )
        keeper = _write(vault, "Patterns/keeper.md")
        note_map = vault_doctor.build_note_map([note, keeper])
        issues = [
            vault_doctor.Issue(
                note,
                "warning",
                "BROKEN_WIKILINK",
                "[[par-rt-db]] does not resolve to any vault note",
            )
        ]

        content, became_orphan = vault_doctor._auto_repair_broken_wikilinks(
            note, issues, note_map
        )

        assert content is not None
        assert "-probello" not in content
        assert "[[keeper]]" in content
        assert not became_orphan
