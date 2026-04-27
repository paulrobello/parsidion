"""ARC-006: Unit tests for vault_doctor.py non-Claude code paths.

Tests validators, link parsers, state management, tag deduplication logic,
prefix cluster detection, and migration logic using tmp_path fixtures.
"""

from datetime import date
from pathlib import Path

import pytest

import vault_common
import vault_doctor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point vault_common and vault_doctor at a temporary vault."""
    monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
    monkeypatch.setattr(vault_doctor, "_vault_path", tmp_path)
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """Return the tmp vault path and create standard dirs."""
    for d in vault_common.VAULT_DIRS:
        (tmp_path / d).mkdir(exist_ok=True)
    return tmp_path


def _write_note(vault: Path, rel_path: str, content: str) -> Path:
    """Helper: write a note file and return its Path."""
    full = vault / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return full


def test_repair_note_uses_small_tier_backend(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    note = _write_note(vault, "Patterns/broken.md", "# Broken\n")
    issue = vault_doctor.Issue(
        path=note,
        severity="error",
        code="MISSING_FRONTMATTER",
        message="Missing frontmatter",
    )

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "---\ntitle: Fixed\ntags: [test]\n---\n# Fixed\n"

    monkeypatch.setattr(vault_doctor.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result, status = vault_doctor.repair_note(
        note, [issue], model=None, timeout=10, vault_path=vault
    )

    assert result == "---\ntitle: Fixed\ntags: [test]\n---\n# Fixed"
    assert status == "fixed"
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["timeout"] == 10
    assert calls[0]["purpose"] == "vault-doctor"
    assert calls[0]["vault"] == vault


# ---------------------------------------------------------------------------
# Issue dataclass
# ---------------------------------------------------------------------------


class TestIssueDataclass:
    def test_issue_fields(self, vault: Path) -> None:
        issue = vault_doctor.Issue(
            path=vault / "test.md",
            severity="error",
            code="MISSING_FIELD",
            message="Required field 'date' is absent",
        )
        assert issue.severity == "error"
        assert issue.code == "MISSING_FIELD"


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


class TestStateManagement:
    def test_load_state_missing_file(self, vault: Path) -> None:
        state = vault_doctor.load_state(vault)
        assert state == {"last_run": None, "notes": {}}

    def test_save_and_load_state(self, vault: Path) -> None:
        state = {"last_run": None, "notes": {"test.md": {"status": "ok"}}}
        vault_doctor.save_state(state, vault)
        loaded = vault_doctor.load_state(vault)
        assert loaded["notes"]["test.md"]["status"] == "ok"
        assert loaded["last_run"] is not None

    def test_should_skip_ok_recent(self, vault: Path) -> None:
        today = date.today().isoformat()
        state = {
            "notes": {
                "test.md": {
                    "status": "ok",
                    "last_checked": today,
                    "issues": [],
                }
            }
        }
        assert vault_doctor.should_skip("test.md", state) is True

    def test_should_skip_needs_review(self, vault: Path) -> None:
        state = {"notes": {"test.md": {"status": "needs_review"}}}
        assert vault_doctor.should_skip("test.md", state) is True

    def test_should_not_skip_fixed(self, vault: Path) -> None:
        state = {"notes": {"test.md": {"status": "fixed"}}}
        assert vault_doctor.should_skip("test.md", state) is False

    def test_should_not_skip_unknown(self, vault: Path) -> None:
        state = {"notes": {}}
        assert vault_doctor.should_skip("new.md", state) is False


# ---------------------------------------------------------------------------
# check_note — frontmatter validation
# ---------------------------------------------------------------------------


class TestCheckNote:
    def test_missing_frontmatter(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/no-fm.md", "Just plain text\n")
        note_map = vault_doctor.build_note_map([note])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "MISSING_FRONTMATTER" in codes

    def test_valid_note_no_issues(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            "tags: [python]\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            "---\n\n"
            "# Valid Note\n\nSome content.\n"
        )
        other = _write_note(
            vault,
            "Patterns/other-note.md",
            "---\ndate: 2026-01-01\ntype: pattern\n---\n",
        )
        note = _write_note(vault, "Patterns/valid.md", content)
        note_map = vault_doctor.build_note_map([note, other])
        issues = vault_doctor.check_note(note, note_map, vault)
        assert len(issues) == 0

    def test_missing_required_fields(self, vault: Path) -> None:
        content = "---\ndate: 2026-03-25\n---\n\n# Test\n"
        note = _write_note(vault, "Patterns/missing.md", content)
        note_map = vault_doctor.build_note_map([note])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "MISSING_FIELD" in codes

    def test_invalid_type(self, vault: Path) -> None:
        content = '---\ndate: 2026-03-25\ntype: foobar\nconfidence: high\nrelated: ["[[x]]"]\n---\n\n# Test\n'
        x = _write_note(
            vault, "Patterns/x.md", "---\ndate: 2026-01-01\ntype: pattern\n---\n"
        )
        note = _write_note(vault, "Patterns/bad-type.md", content)
        note_map = vault_doctor.build_note_map([note, x])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "INVALID_TYPE" in codes

    def test_invalid_date(self, vault: Path) -> None:
        content = '---\ndate: not-a-date\ntype: pattern\nconfidence: high\nrelated: ["[[x]]"]\n---\n\n# Test\n'
        x = _write_note(
            vault, "Patterns/x.md", "---\ndate: 2026-01-01\ntype: pattern\n---\n"
        )
        note = _write_note(vault, "Patterns/bad-date.md", content)
        note_map = vault_doctor.build_note_map([note, x])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "INVALID_DATE" in codes

    def test_orphan_note(self, vault: Path) -> None:
        content = "---\ndate: 2026-03-25\ntype: pattern\nconfidence: high\nrelated: []\n---\n\n# Test\n"
        note = _write_note(vault, "Patterns/orphan.md", content)
        note_map = vault_doctor.build_note_map([note])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "ORPHAN_NOTE" in codes

    def test_broken_wikilink(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[nonexistent]]"]\n'
            "---\n\n"
            "# Test\n\nSee [[nonexistent]] for details.\n"
        )
        note = _write_note(vault, "Patterns/broken.md", content)
        note_map = vault_doctor.build_note_map([note])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "BROKEN_WIKILINK" in codes

    def test_heading_mismatch(self, vault: Path) -> None:
        content = '---\ndate: 2026-03-25\ntype: pattern\nconfidence: high\nrelated: ["[[x]]"]\n---\n\n## Only H2 Heading\n'
        x = _write_note(
            vault, "Patterns/x.md", "---\ndate: 2026-01-01\ntype: pattern\n---\n"
        )
        note = _write_note(vault, "Patterns/h2-only.md", content)
        note_map = vault_doctor.build_note_map([note, x])
        issues = vault_doctor.check_note(note, note_map, vault)
        codes = [i.code for i in issues]
        assert "HEADING_MISMATCH" in codes

    def test_daily_note_fewer_requirements(self, vault: Path) -> None:
        """Daily notes only require date and type, not confidence/related."""
        content = (
            "---\ndate: 2026-03-25\ntype: daily\ntags: [daily]\n---\n\n## Sessions\n"
        )
        note = _write_note(vault, "Daily/2026-03/25-testuser.md", content)
        note_map = vault_doctor.build_note_map([note])
        issues = vault_doctor.check_note(note, note_map, vault)
        # Should not have MISSING_FIELD for confidence/related
        codes = [i.code for i in issues]
        assert "MISSING_FIELD" not in codes


# ---------------------------------------------------------------------------
# resolve_wikilink
# ---------------------------------------------------------------------------


class TestResolveWikilink:
    def test_exact_match(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/my-note.md", "# Test\n")
        note_map = vault_doctor.build_note_map([note])
        assert vault_doctor.resolve_wikilink("my-note", note_map) is True

    def test_case_insensitive(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/My-Note.md", "# Test\n")
        note_map = vault_doctor.build_note_map([note])
        assert vault_doctor.resolve_wikilink("my-note", note_map) is True

    def test_nonexistent(self, vault: Path) -> None:
        note_map: dict[str, list[Path]] = {}
        assert vault_doctor.resolve_wikilink("nope", note_map) is False

    def test_display_alias(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/target.md", "# Test\n")
        note_map = vault_doctor.build_note_map([note])
        assert vault_doctor.resolve_wikilink("target|display text", note_map) is True

    def test_section_anchor(self, vault: Path) -> None:
        note = _write_note(vault, "Patterns/target.md", "# Test\n")
        note_map = vault_doctor.build_note_map([note])
        assert vault_doctor.resolve_wikilink("target#section", note_map) is True

    def test_empty_link(self, vault: Path) -> None:
        note_map: dict[str, list[Path]] = {}
        assert vault_doctor.resolve_wikilink("", note_map) is True


# ---------------------------------------------------------------------------
# build_note_map
# ---------------------------------------------------------------------------


class TestBuildNoteMap:
    def test_groups_by_lowercase_stem(self, vault: Path) -> None:
        n1 = _write_note(vault, "Patterns/Foo.md", "# Foo\n")
        n2 = _write_note(vault, "Research/foo.md", "# foo\n")
        note_map = vault_doctor.build_note_map([n1, n2])
        assert len(note_map["foo"]) == 2

    def test_empty_list(self, vault: Path) -> None:
        note_map = vault_doctor.build_note_map([])
        assert note_map == {}


# ---------------------------------------------------------------------------
# Tag deduplication logic
# ---------------------------------------------------------------------------


class TestFindTagDuplicates:
    def test_plural_singular(self) -> None:
        counts = {"hook": 5, "hooks": 3}
        pairs = vault_doctor._find_tag_duplicates(counts)
        assert len(pairs) == 1
        keep, away, reason = pairs[0]
        assert keep == "hook"
        assert away == "hooks"
        assert reason == "plural/singular"

    def test_hyphen_underscore(self) -> None:
        counts = {"par-ai-core": 3, "par_ai_core": 2}
        pairs = vault_doctor._find_tag_duplicates(counts)
        assert len(pairs) == 1
        keep, away, reason = pairs[0]
        assert keep == "par-ai-core"
        assert away == "par_ai_core"
        assert reason == "hyphen/underscore"

    def test_case_duplicate(self) -> None:
        counts = {"Python": 1, "python": 5}
        pairs = vault_doctor._find_tag_duplicates(counts)
        assert len(pairs) == 1

    def test_no_duplicates(self) -> None:
        counts = {"python": 5, "rust": 3, "typescript": 2}
        pairs = vault_doctor._find_tag_duplicates(counts)
        assert len(pairs) == 0


class TestReplaceTagInNote:
    def test_inline_list_replacement(self, vault: Path) -> None:
        content = "---\ntags: [hooks, python]\n---\n\n# Test\n"
        note = _write_note(vault, "Patterns/test.md", content)
        result = vault_doctor._replace_tag_in_note(note, "hooks", "hook")
        assert result is True
        updated = note.read_text(encoding="utf-8")
        assert "hook" in updated
        assert "hooks" not in updated

    def test_quoted_inline_list(self, vault: Path) -> None:
        content = '---\ntags: ["hooks", "python"]\n---\n\n# Test\n'
        note = _write_note(vault, "Patterns/test2.md", content)
        result = vault_doctor._replace_tag_in_note(note, "hooks", "hook")
        assert result is True
        updated = note.read_text(encoding="utf-8")
        assert '"hook"' in updated

    def test_no_match_returns_false(self, vault: Path) -> None:
        content = "---\ntags: [python, rust]\n---\n\n# Test\n"
        note = _write_note(vault, "Patterns/test3.md", content)
        result = vault_doctor._replace_tag_in_note(note, "hooks", "hook")
        assert result is False


# ---------------------------------------------------------------------------
# Prefix cluster detection
# ---------------------------------------------------------------------------


class TestFindPrefixClusters:
    def test_finds_first_word_cluster(self, vault: Path) -> None:
        _write_note(vault, "Patterns/redis-caching.md", "# Redis Caching\n")
        _write_note(vault, "Patterns/redis-pubsub.md", "# Redis PubSub\n")
        _write_note(vault, "Patterns/redis-streams.md", "# Redis Streams\n")
        all_notes = list(vault_common.all_vault_notes(vault))
        clusters = vault_doctor.find_prefix_clusters(all_notes, vault)
        # Should find the redis cluster
        prefixes = [prefix for _, prefix, _, _ in clusters]
        assert "redis" in prefixes

    def test_ignores_small_groups(self, vault: Path) -> None:
        _write_note(vault, "Patterns/redis-caching.md", "# Redis Caching\n")
        _write_note(vault, "Patterns/redis-pubsub.md", "# Redis PubSub\n")
        # Only 2 notes -- below PREFIX_CLUSTER_MIN (3)
        all_notes = list(vault_common.all_vault_notes(vault))
        clusters = vault_doctor.find_prefix_clusters(all_notes, vault)
        assert len(clusters) == 0

    def test_finds_exact_stem_cluster(self, vault: Path) -> None:
        _write_note(vault, "Patterns/my-project.md", "# My Project\n")
        _write_note(vault, "Patterns/my-project-setup.md", "# Setup\n")
        _write_note(vault, "Patterns/my-project-deploy.md", "# Deploy\n")
        all_notes = list(vault_common.all_vault_notes(vault))
        clusters = vault_doctor.find_prefix_clusters(all_notes, vault)
        # Should find exact-stem cluster where base_note is not None
        exact = [(p, b) for _, p, _, b in clusters if b is not None]
        assert any(p == "my-project" for p, _ in exact)


# ---------------------------------------------------------------------------
# Heading auto-fix
# ---------------------------------------------------------------------------


class TestAutoFixHeadings:
    def test_promotes_h2_to_h1(self, vault: Path) -> None:
        content = (
            "---\ndate: 2026-03-25\ntype: pattern\n---\n\n## My Heading\n\nBody text.\n"
        )
        note = _write_note(vault, "Patterns/promo.md", content)
        result = vault_doctor._auto_fix_headings(note)
        assert result is True
        updated = note.read_text(encoding="utf-8")
        assert "# My Heading" in updated
        assert "## My Heading" not in updated

    def test_no_change_if_h1_exists(self, vault: Path) -> None:
        content = (
            "---\ndate: 2026-03-25\ntype: pattern\n---\n\n# Existing H1\n\n## Sub\n"
        )
        note = _write_note(vault, "Patterns/has-h1.md", content)
        result = vault_doctor._auto_fix_headings(note)
        assert result is False


# ---------------------------------------------------------------------------
# Redundant prefix detection
# ---------------------------------------------------------------------------


class TestFindRedundantPrefixes:
    def test_finds_redundant(self, vault: Path) -> None:
        subfolder = vault / "Projects" / "myapp"
        subfolder.mkdir(parents=True)
        note = _write_note(vault, "Projects/myapp/myapp-overview.md", "# Overview\n")
        all_notes = [note]
        pairs = vault_doctor._find_redundant_prefixes(all_notes, vault)
        assert len(pairs) == 1
        old, new = pairs[0]
        assert old.name == "myapp-overview.md"
        assert new.name == "overview.md"

    def test_ignores_non_redundant(self, vault: Path) -> None:
        subfolder = vault / "Projects" / "myapp"
        subfolder.mkdir(parents=True)
        note = _write_note(vault, "Projects/myapp/overview.md", "# Overview\n")
        all_notes = [note]
        pairs = vault_doctor._find_redundant_prefixes(all_notes, vault)
        assert len(pairs) == 0


# ---------------------------------------------------------------------------
# Dedup related links
# ---------------------------------------------------------------------------


class TestDedupRelatedLinks:
    def test_removes_duplicates(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            'related: ["[[note-a]]", "[[note-b]]", "[[note-a]]"]\n'
            "---\n\n# Test\n"
        )
        _write_note(vault, "Patterns/dup.md", content)
        fixed = vault_doctor.dedup_related_links(dry_run=False, vault_path=vault)
        assert fixed == 1
        updated = (vault / "Patterns" / "dup.md").read_text(encoding="utf-8")
        # Should have exactly 2 entries, not 3
        assert updated.count("[[note-a]]") == 1

    def test_no_change_when_clean(self, vault: Path) -> None:
        content = (
            "---\n"
            "date: 2026-03-25\n"
            "type: pattern\n"
            'related: ["[[note-a]]", "[[note-b]]"]\n'
            "---\n\n# Test\n"
        )
        _write_note(vault, "Patterns/clean.md", content)
        fixed = vault_doctor.dedup_related_links(dry_run=False, vault_path=vault)
        assert fixed == 0
