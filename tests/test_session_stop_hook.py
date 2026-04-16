"""Unit tests for session_stop_hook.py functions.

Tests cover:
- detect_categories (via vault_common) — categorization of transcript texts
- append_to_pending (via vault_common) — JSONL queue writes and deduplication

These tests import vault_common directly (the canonical implementation) and
use tmp_path for all file I/O to avoid touching the real vault.
"""

import json
from pathlib import Path

import pytest

import vault_common


# ---------------------------------------------------------------------------
# detect_categories
# ---------------------------------------------------------------------------


class TestDetectCategories:
    """Tests for vault_common.detect_categories."""

    def test_empty_list_returns_empty(self) -> None:
        result = vault_common.detect_categories([])
        assert result == {}

    def test_no_keywords_returns_empty(self) -> None:
        texts = ["Hello world", "This is a routine update."]
        result = vault_common.detect_categories(texts)
        assert result == {}

    def test_error_fix_keyword(self) -> None:
        texts = ["I found the root cause of the issue."]
        result = vault_common.detect_categories(texts)
        assert "error_fix" in result

    def test_error_fix_multiple_keywords(self) -> None:
        texts = ["Fixed the bug. The error was in the config parser."]
        result = vault_common.detect_categories(texts)
        assert "error_fix" in result

    def test_research_keyword(self) -> None:
        texts = ["According to the documentation, this is the recommended approach."]
        result = vault_common.detect_categories(texts)
        assert "research" in result

    def test_pattern_keyword(self) -> None:
        texts = ["This is a reusable pattern for handling async callbacks."]
        result = vault_common.detect_categories(texts)
        assert "pattern" in result

    def test_config_setup_keyword(self) -> None:
        texts = ["I configured the environment and initialized the project."]
        result = vault_common.detect_categories(texts)
        assert "config_setup" in result

    def test_multiple_categories_detected(self) -> None:
        texts = [
            "Root cause was a missing dependency.",  # error_fix
            "The documentation explains the pattern to follow.",  # research + pattern
        ]
        result = vault_common.detect_categories(texts)
        # At least one category must be detected
        assert len(result) >= 1

    def test_excerpt_stored_in_category(self) -> None:
        texts = ["Root cause was a missing import."]
        result = vault_common.detect_categories(texts)
        assert "error_fix" in result
        # The value should be a list (possibly with excerpt)
        assert isinstance(result["error_fix"], list)

    def test_case_insensitive_matching(self) -> None:
        texts = ["ROOT CAUSE: the variable was not initialized."]
        result = vault_common.detect_categories(texts)
        assert "error_fix" in result

    def test_single_keyword_match_is_sufficient(self) -> None:
        texts = ["The issue was resolved."]
        result = vault_common.detect_categories(texts)
        # "the issue was" is a keyword in error_fix
        assert "error_fix" in result

    def test_returns_dict_type(self) -> None:
        result = vault_common.detect_categories(["some text"])
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# parse_transcript_lines
# ---------------------------------------------------------------------------


class TestParseTranscriptLines:
    """Tests for vault_common.parse_transcript_lines."""

    def test_parses_claude_assistant_entry(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Root cause was X."}],
                },
            }
        )
        result = vault_common.parse_transcript_lines([line])
        assert result == ["Root cause was X."]

    def test_parses_pi_assistant_message_entry(self) -> None:
        line = json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Found that docs require Y."}],
                },
            }
        )
        result = vault_common.parse_transcript_lines([line])
        assert result == ["Found that docs require Y."]

    def test_ignores_non_assistant_entries(self) -> None:
        user_line = json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Please fix this."}],
                },
            }
        )
        tool_line = json.dumps(
            {
                "type": "message",
                "message": {
                    "role": "toolResult",
                    "content": [{"type": "text", "text": "tool output"}],
                },
            }
        )
        result = vault_common.parse_transcript_lines([user_line, tool_line])
        assert result == []


# ---------------------------------------------------------------------------
# append_to_pending
# ---------------------------------------------------------------------------


class TestAppendToPending:
    """Tests for vault_common.append_to_pending."""

    @pytest.fixture(autouse=True)
    def clear_vault_cache(self) -> None:
        """Clear resolve_vault lru_cache so monkeypatching VAULT_ROOT takes effect."""
        vault_common.resolve_vault.cache_clear()  # type: ignore[union-attr]

    def test_writes_jsonl_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "abc123.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        categories = {"error_fix": ["root cause was X"]}
        vault_common.append_to_pending(
            transcript_path=transcript,
            project="test-project",
            categories=categories,
        )

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        lines = [
            line.strip()
            for line in pending.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["session_id"] == "abc123"
        assert entry["project"] == "test-project"
        assert "error_fix" in entry["categories"]

    def test_deduplication_same_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "session-xyz.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        categories = {"error_fix": []}
        vault_common.append_to_pending(transcript, "proj", categories)
        vault_common.append_to_pending(transcript, "proj", categories)

        pending = tmp_path / "pending_summaries.jsonl"
        lines = [
            ln.strip()
            for ln in pending.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 1, "Duplicate session_id must not be appended twice"

    def test_no_significant_category_skips_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "routine.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        # config_setup alone is not in the significant set {error_fix, research, pattern}
        categories = {"config_setup": []}
        vault_common.append_to_pending(transcript, "proj", categories)

        pending = tmp_path / "pending_summaries.jsonl"
        # File either doesn't exist or is empty — nothing written
        if pending.exists():
            lines = [
                ln
                for ln in pending.read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            assert len(lines) == 0

    def test_force_bypasses_significance_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "forced.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        categories = {"config_setup": []}
        vault_common.append_to_pending(transcript, "proj", categories, force=True)

        pending = tmp_path / "pending_summaries.jsonl"
        assert pending.exists()
        lines = [
            ln.strip()
            for ln in pending.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 1

    def test_entry_has_required_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "full-entry.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        categories = {"research": ["found in docs"]}
        vault_common.append_to_pending(transcript, "my-project", categories)

        pending = tmp_path / "pending_summaries.jsonl"
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert "session_id" in entry
        assert "transcript_path" in entry
        assert "project" in entry
        assert "categories" in entry
        assert "timestamp" in entry
        assert "source" in entry

    def test_source_defaults_to_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "src-test.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        vault_common.append_to_pending(transcript, "proj", {"error_fix": []})

        pending = tmp_path / "pending_summaries.jsonl"
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["source"] == "session"

    def test_source_subagent_stored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)
        transcript = tmp_path / "sub-test.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        vault_common.append_to_pending(
            transcript,
            "proj",
            {"error_fix": []},
            source="subagent",
            agent_type="Explore",
        )

        pending = tmp_path / "pending_summaries.jsonl"
        entry = json.loads(pending.read_text(encoding="utf-8").strip())
        assert entry["source"] == "subagent"
        assert entry.get("agent_type") == "Explore"

    def test_multiple_different_sessions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)

        for name in ("session-a", "session-b", "session-c"):
            t = tmp_path / f"{name}.jsonl"
            t.write_text("{}\n", encoding="utf-8")
            vault_common.append_to_pending(t, "proj", {"error_fix": []})

        pending = tmp_path / "pending_summaries.jsonl"
        lines = [
            ln.strip()
            for ln in pending.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        assert len(lines) == 3
