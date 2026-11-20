"""Regression tests for vault hygiene hardening fixes.

Covers all 8 root-cause fixes identified from auditing ParsidionVault:
1. Worktree-aware get_project_name() & ephemeral agent-<hex>/wf-<hex> rejection
2. Summarizer salvage/sanitization (body fence unwrap, stray tokens, trailing JSON,
   refusal/skip rejection, redundant slug-collision skip)
3. Lexical & in-flight batch dedup fallback when embeddings.enabled=false
4. Dot-directory exclusion in _walk_vault_notes (.pytest_cache, .claude, etc.)
5. pre_compact_hook synthetic XML skip & valid Daily frontmatter creation
6. vault_metrics path-qualified wikilinks + vault_health tag/embedding accuracy
7. doctor mtime-aware should_skip, glued-closer splice, stray/fence/agent checks & auto-fix
8. vault_merge._merge_notes preserving latest date, max confidence, unioned sources & triggers
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import pre_compact_hook  # noqa: E402
import parsidion.core.vault_health as vault_health  # noqa: E402
import parsidion.core.vault_hooks as vault_hooks  # noqa: E402
import parsidion.core.vault_index as vault_index  # noqa: E402
import parsidion.core.vault_metrics as vault_metrics  # noqa: E402
import parsidion.doctor._state as doctor_state  # noqa: E402
import parsidion.doctor.check as doctor_check  # noqa: E402
import parsidion.doctor.frontmatter as doctor_fm  # noqa: E402
import parsidion.doctor.links as doctor_links  # noqa: E402
import parsidion.summarizer.dedup as summarizer_dedup  # noqa: E402
import parsidion.summarizer.notes as summarizer_notes  # noqa: E402
import parsidion.vault_merge as vault_merge  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Worktree-aware get_project_name & ephemeral agent ID detection
# ---------------------------------------------------------------------------


class TestWorktreeProjectDetection:
    def test_resolves_git_worktree_to_main_repo_name(self, tmp_path: Path) -> None:
        main_repo = tmp_path / "par-term"
        wt_gitdir = main_repo / ".git" / "worktrees" / "agent-af1ab6ce50dba80a1"
        wt_gitdir.mkdir(parents=True)

        worktree = main_repo / ".claude" / "worktrees" / "agent-af1ab6ce50dba80a1"
        sub = worktree / "src" / "nested"
        sub.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

        assert vault_hooks.get_project_name(str(worktree)) == "par-term"
        assert vault_hooks.get_project_name(str(sub)) == "par-term"

    def test_resolves_relative_gitdir_pointer(self, tmp_path: Path) -> None:
        main_repo = tmp_path / "my-repo"
        wt_gitdir = main_repo / ".git" / "worktrees" / "agent-deadbeef1234"
        wt_gitdir.mkdir(parents=True)
        worktree = main_repo / ".claude" / "worktrees" / "agent-deadbeef1234"
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(
            "gitdir: ../../../.git/worktrees/agent-deadbeef1234\n", encoding="utf-8"
        )
        assert vault_hooks.get_project_name(str(worktree)) == "my-repo"

    @pytest.mark.parametrize(
        "candidate,expected",
        [
            ("agent-af1ab6ce50dba80a1", True),
            ("worktree-agent-deadbeef01", True),
            ("wf-494de409-e3a-1", True),
            ("par-term", False),
            ("agent-sdk", False),
            ("ai-agent", False),
        ],
    )
    def test_is_ephemeral_agent_id(self, candidate: str, expected: bool) -> None:
        assert vault_hooks.is_ephemeral_agent_id(candidate) is expected


# ---------------------------------------------------------------------------
# 2. Summarizer salvage, sanitization & refusal rejection
# ---------------------------------------------------------------------------


class TestSummarizerSanitization:
    def test_inject_project_tag_skips_ephemeral_agent_id(self) -> None:
        note = "---\ndate: 2026-11-19\ntype: pattern\ntags: [rust]\n---\n\n# Title\nBody.\n"
        out = summarizer_notes.inject_project_tag(note, "agent-af1ab6ce50dba80a1")
        assert "agent-af1ab6ce50dba80a1" not in out

    def test_synthesize_frontmatter_refuses_json_and_refusals(self) -> None:
        skip_json = '{"decision": "skip", "reason": "nothing novel"}'
        refusal = "No chart is needed for this note.\n\nJust bullets."
        perm = "Please approve the write permission for the vault file."
        assert (
            summarizer_notes._synthesize_frontmatter_if_missing(skip_json) == skip_json
        )
        assert summarizer_notes._synthesize_frontmatter_if_missing(refusal) == refusal
        assert summarizer_notes._synthesize_frontmatter_if_missing(perm) == perm

    def test_write_note_rejects_refusal_body(self, tmp_path: Path) -> None:
        bad = (
            "---\ndate: 2026-11-19\ntype: pattern\ntags: [rust]\n---\n\n"
            '{"decision": "skip", "reason": "routine"}\n'
        )
        path, reason = summarizer_notes.write_note_with_reason(bad, False, tmp_path)
        assert path is None
        assert "refusal" in reason.lower() or "json" in reason.lower()

    def test_sanitize_unwraps_body_fence_strips_stray_tokens_and_trailing_json(
        self, tmp_path: Path
    ) -> None:
        raw = (
            "--- \n"
            "date: 2026-11-19 \n"
            "type: pattern \n"
            "project: agent-af1ab6ce50dba80a1 \n"
            "tags: [rust] \n"
            "--- \n"
            "}\n"
            "```markdown\n"
            "# Clean Pattern Title\n\n"
            "## Summary\n\n"
            "Use balanced inner blocks:\n\n"
            "```rust\n"
            "fn ok() {}\n"
            "```\n"
            "```\n\n"
            '({"decision": "skip", "reason": "placeholder"})\n'
        )
        path, reason = summarizer_notes.write_note_with_reason(raw, False, tmp_path)
        assert path is not None and reason == ""
        written = path.read_text(encoding="utf-8")
        assert written.startswith("---\ndate: 2026-11-19\n")
        assert "agent-af1ab6ce50dba80a1" not in written
        assert "\n}\n" not in written
        assert "```markdown" not in written
        assert "```rust\nfn ok() {}\n```" in written
        assert '"decision"' not in written

    def test_slug_collision_skips_high_overlap_duplicate_append(
        self, tmp_path: Path
    ) -> None:
        patterns = tmp_path / "Patterns"
        patterns.mkdir()
        body_words = " ".join(f"architecture{i}" for i in range(30))
        note = (
            "---\ndate: 2026-11-19\ntype: pattern\ntags: [rust]\n---\n\n"
            f"# Repeated Pattern\n\n{body_words}\n"
        )
        p1, _ = summarizer_notes.write_note_with_reason(note, False, tmp_path)
        assert p1 is not None
        first_text = p1.read_text(encoding="utf-8")
        p2, _ = summarizer_notes.write_note_with_reason(note, False, tmp_path)
        assert p2 is not None
        assert p2 == p1
        assert "## Session update" not in p2.read_text(encoding="utf-8")
        assert p2.read_text(encoding="utf-8") == first_text


# ---------------------------------------------------------------------------
# 3. Lexical & in-flight batch dedup fallback
# ---------------------------------------------------------------------------


class TestLexicalAndInFlightDedup:
    def test_finds_in_flight_and_note_index_candidates_when_embeddings_disabled(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "config.yaml").write_text(
            "embeddings:\n  enabled: false\n", encoding="utf-8"
        )
        db_path = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """CREATE TABLE note_index (
                stem TEXT PRIMARY KEY, path TEXT, folder TEXT, title TEXT,
                summary TEXT, tags TEXT, note_type TEXT, project TEXT,
                confidence TEXT, mtime REAL, related TEXT, is_stale INTEGER,
                incoming_links INTEGER, date TEXT, prompt_version TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO note_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, '')",
            (
                "ios-dvh-viewport-clipping",
                str(tmp_path / "Debugging/ios-dvh-viewport-clipping.md"),
                "Debugging",
                "Fixing iOS dvh Viewport Clipping",
                "Replaced 100vh with 100dvh to fix Safari dock viewport clipping on iOS",
                "ios,css",
                "debugging",
                "par-web",
                "high",
                1000.0,
                "",
                "2026-11-19",
            ),
        )
        conn.commit()
        conn.close()

        in_flight = [
            (
                "godot-stale-skeleton-pose",
                "Godot Stale Skeleton Pose Carryover",
                "Reset skeleton rest pose across AnimationPlayer clips in Godot",
            )
        ]

        # Finds note_index match even with embeddings disabled
        cands = summarizer_dedup._find_dedup_candidates(
            "par-web safari dock viewport clipping 100vh 100dvh ios",
            tmp_path,
            project="par-web",
            in_flight=in_flight,
        )
        assert any(stem == "ios-dvh-viewport-clipping" for stem, _, _ in cands)

        # Finds in-flight sibling match written earlier in the same batch
        cands2 = summarizer_dedup._find_dedup_candidates(
            "godot skeleton rest pose carryover animationplayer clips",
            tmp_path,
            project="par-cozy",
            in_flight=in_flight,
        )
        assert any(stem == "godot-stale-skeleton-pose" for stem, _, _ in cands2)


# ---------------------------------------------------------------------------
# 4. Dot-directory exclusion in _walk_vault_notes
# ---------------------------------------------------------------------------


class TestWalkExcludesDotDirs:
    def test_skips_pytest_cache_and_hidden_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "Patterns").mkdir()
        (tmp_path / "Patterns" / "good-note.md").write_text(
            "# Good\n", encoding="utf-8"
        )
        (tmp_path / ".pytest_cache").mkdir()
        (tmp_path / ".pytest_cache" / "README.md").write_text(
            "# Cache\n", encoding="utf-8"
        )
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "scratch.md").write_text(
            "# Scratch\n", encoding="utf-8"
        )

        notes = vault_index._walk_vault_notes(tmp_path)
        rels = {str(p.relative_to(tmp_path)) for p in notes}
        assert rels == {"Patterns/good-note.md"}


# ---------------------------------------------------------------------------
# 5. pre_compact_hook XML filtering & Daily frontmatter creation
# ---------------------------------------------------------------------------


class TestPreCompactHardening:
    def test_extract_user_task_skips_system_reminder_and_command_name(self) -> None:
        lines = [
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "Refactor the auth middleware to support OAuth PKCE",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "<command-name>/clear</command-name>",
                    },
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": "<system-reminder>\nInternal hook note\n</system-reminder>",
                    },
                }
            ),
        ]
        assert (
            pre_compact_hook.extract_user_task(lines)
            == "Refactor the auth middleware to support OAuth PKCE"
        )

    def test_append_snapshot_creates_daily_with_valid_frontmatter(
        self, tmp_path: Path
    ) -> None:
        pre_compact_hook.append_snapshot_to_daily(
            project="par-term",
            task_summary="Fix worktree detection",
            recent_files=["src/main.rs"],
            cwd=str(tmp_path),
            vault_path=tmp_path,
        )
        daily_files = list((tmp_path / "Daily").rglob("*.md"))
        assert len(daily_files) == 1
        content = daily_files[0].read_text(encoding="utf-8")
        fm = vault_index.parse_frontmatter(content)
        assert fm.get("type") == "daily"
        assert fm.get("date")
        assert "## Pre-Compact Snapshot" in content


# ---------------------------------------------------------------------------
# 6. vault_metrics path-qualified wikilinks & vault_health accuracy
# ---------------------------------------------------------------------------


class TestVaultHealthAccuracy:
    def test_collect_graph_accepts_path_qualified_wikilinks(
        self, tmp_path: Path
    ) -> None:
        db_path = tmp_path / "embeddings.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """CREATE TABLE note_index (
                stem TEXT PRIMARY KEY, path TEXT, title TEXT, folder TEXT,
                incoming_links INTEGER, related TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO note_index VALUES (?, ?, ?, ?, 1, '')",
            (
                "overview",
                str(tmp_path / "Projects/parvitar/overview.md"),
                "Overview",
                "Projects",
            ),
        )
        conn.execute(
            "INSERT INTO note_index VALUES (?, ?, ?, ?, 0, ?)",
            (
                "client-note",
                str(tmp_path / "Patterns/client-note.md"),
                "Client Note",
                "Patterns",
                "parvitar/overview",
            ),
        )
        conn.commit()
        graph = vault_metrics.collect_graph(conn)
        conn.close()
        assert graph["total_targets"] == 1
        assert graph["dangling_targets"] == 0

    def test_near_duplicate_tags_ignores_short_acronyms_and_catches_real_dups(
        self,
    ) -> None:
        tags = [
            "cpu",
            "gpu",
            "rust",
            "rustc",
            "sql",
            "sqlx",
            "tls",
            "tts",
            "macos-26",
            "macos-27",
            "hook",
            "hooks",
            "par-splat",
            "parsplat",
            "openetelemetry",
            "opentelemetry",
        ]
        pairs = set(vault_health._tag_pairs_near_duplicate(tags))
        assert ("hook", "hooks") in pairs
        assert ("par-splat", "parsplat") in pairs
        assert ("openetelemetry", "opentelemetry") in pairs
        assert ("cpu", "gpu") not in pairs
        assert ("rust", "rustc") not in pairs
        assert ("sql", "sqlx") not in pairs
        assert ("tls", "tts") not in pairs
        assert ("macos-26", "macos-27") not in pairs

    def test_embedding_coverage_respects_disabled_config(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(
            "embeddings:\n  enabled: false\n", encoding="utf-8"
        )
        score = vault_health.score_embedding_coverage(tmp_path)
        assert score.score == 100
        assert "disabled" in score.detail
        assert score.action is None


# ---------------------------------------------------------------------------
# 7. doctor mtime-aware skip, glued closer splice, stray/fence/agent checks
# ---------------------------------------------------------------------------


class TestDoctorHardening:
    def test_should_skip_invalidates_when_file_mtime_advances(
        self, tmp_path: Path
    ) -> None:
        note = tmp_path / "rule.md"
        note.write_text("# v1\n", encoding="utf-8")
        old_mtime = round(note.stat().st_mtime, 3)
        state = {"notes": {"rule.md": {"status": "skipped", "mtime": old_mtime}}}
        assert doctor_state.should_skip("rule.md", state, path=note) is True

        # Touch mtime forward -> must re-scan even though status was "skipped"
        os.utime(note, (old_mtime + 10, old_mtime + 10))
        assert doctor_state.should_skip("rule.md", state, path=note) is False

    def test_splice_frontmatter_handles_glued_closing_delimiter(self) -> None:
        original = (
            "---\ndate: 2026-11-15\ntype: debugging\n"
            "tags: [three-js, webxr]---\n\n# Real Heading\n\nBody text.\n"
        )
        repaired = (
            "---\ndate: 2026-11-15\ntype: debugging\n"
            "tags: [three-js, webxr]\nconfidence: high\n"
            'related: ["[[other]]"]\n---\n\n# Real Heading\n'
        )
        spliced = doctor_fm.splice_frontmatter_onto_original(repaired, original)
        assert spliced.count("tags: [three-js, webxr]") == 1
        assert "Body text." in spliced

    def test_check_and_autofix_stray_token_body_fence_and_agent_tag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        patterns = tmp_path / "Patterns"
        patterns.mkdir()
        other = patterns / "other-note.md"
        other.write_text(
            "---\ndate: 2026-11-19\ntype: pattern\ntags: [rust]\n"
            'confidence: high\nrelated: ["[[bad-note]]"]\n---\n\n# Other\n',
            encoding="utf-8",
        )
        bad = patterns / "bad-note.md"
        bad.write_text(
            "---\ndate: 2026-11-19\ntype: pattern\n"
            "tags: [rust, agent-af1ab6ce50dba80a1]\n"
            "project: agent-af1ab6ce50dba80a1\n"
            'confidence: high\nrelated: ["[[other-note]]"]\n---\n'
            "}\n"
            "```\n"
            "# Bad Note\n\nContent here.\n"
            "```\n",
            encoding="utf-8",
        )
        note_map = doctor_links.build_note_map([other, bad])
        codes = {i.code for i in doctor_check.check_note(bad, note_map, tmp_path)}
        assert "STRAY_BODY_TOKEN" in codes
        assert "EPHEMERAL_AGENT_TAG" in codes

        monkeypatch.setattr(doctor_state, "_vault_path", tmp_path)
        assert doctor_fm._auto_fix_body_and_agent_artifacts(bad) is True
        cleaned = bad.read_text(encoding="utf-8")
        assert "agent-af1ab6ce50dba80a1" not in cleaned
        assert "\n}\n" not in cleaned
        assert "```" not in cleaned
        assert doctor_check.check_note(bad, note_map, tmp_path) == []


# ---------------------------------------------------------------------------
# 8. vault_merge._merge_notes preserves metadata from Note B
# ---------------------------------------------------------------------------


class TestVaultMergeMetadataPreservation:
    def test_merges_latest_date_max_confidence_sources_and_rule_triggers(
        self, tmp_path: Path
    ) -> None:
        p_a = tmp_path / "rule-a.md"
        p_b = tmp_path / "rule-b.md"
        content_a = (
            "---\n"
            "date: 2026-09-10\n"
            "type: rule\n"
            "tags: [booking]\n"
            "triggers: [booking, fleet-booking]\n"
            "confidence: medium\n"
            "sources: [src/a.ts]\n"
            'related: ["[[ref-a]]"]\n'
            "---\n\n# Rule A\n\nBody A.\n"
        )
        content_b = (
            "---\n"
            "date: 2026-11-01\n"
            "type: rule\n"
            "tags: [broadcast]\n"
            "triggers: [broadcast, booking]\n"
            "confidence: high\n"
            "sources: [src/b.ts, src/a.ts]\n"
            'related: ["[[ref-b]]"]\n'
            "---\n\n# Rule B\n\nBody B.\n"
        )
        merged = vault_merge._merge_notes(
            p_a, content_a, p_b, content_b, no_ai=True, vault_path=tmp_path
        )
        fm = vault_index.parse_frontmatter(merged)
        assert str(fm["date"]) == "2026-11-01"
        assert fm["confidence"] == "high"
        assert fm["sources"] == ["src/a.ts", "src/b.ts"]
        assert fm["triggers"] == ["booking", "fleet-booking", "broadcast"]
