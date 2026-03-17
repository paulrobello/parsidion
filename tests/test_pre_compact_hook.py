"""Unit tests for pre_compact_hook.py functions.

Tests cover:
- extract_user_task(lines) — finds the most recent user text message
- extract_file_paths(lines) — finds file paths from tool_use blocks

JSONL fixture lines are built as dicts and serialized with json.dumps
so the tests don't depend on brittle string formatting.
"""

import json
import sys
from pathlib import Path


# Mirror the sys.path.insert pattern used by all hook scripts (ARC-009).
sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent / "skills" / "parsidion-cc" / "scripts"),
)

# Import functions under test directly from pre_compact_hook.
# The module uses sys.path.insert so this import resolves correctly.
import importlib.util

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion-cc" / "scripts"
)
_spec = importlib.util.spec_from_file_location(
    "pre_compact_hook", _SCRIPTS_DIR / "pre_compact_hook.py"
)
assert _spec is not None and _spec.loader is not None
_pre_compact_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pre_compact_hook)  # type: ignore[union-attr]

extract_user_task = _pre_compact_hook.extract_user_task
extract_file_paths = _pre_compact_hook.extract_file_paths


# ---------------------------------------------------------------------------
# Helpers to build JSONL fixture lines
# ---------------------------------------------------------------------------


def _user_text_line(text: str) -> str:
    """Return a JSONL line for a user message with plain string content."""
    entry = {
        "type": "user",
        "message": {
            "role": "user",
            "content": text,
        },
    }
    return json.dumps(entry)


def _user_text_block_line(text: str) -> str:
    """Return a JSONL line for a user message with a list-of-blocks content."""
    entry = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }
    return json.dumps(entry)


def _tool_result_line(output: str) -> str:
    """Return a JSONL line for a user message containing only a tool_result block."""
    entry = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "content": output}],
        },
    }
    return json.dumps(entry)


def _assistant_read_line(file_path: str) -> str:
    """Return a JSONL line for an assistant message using the Read tool."""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": file_path},
                }
            ],
        },
    }
    return json.dumps(entry)


def _assistant_write_line(file_path: str) -> str:
    """Return a JSONL line for an assistant message using the Write tool."""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": file_path, "content": "data"},
                }
            ],
        },
    }
    return json.dumps(entry)


def _assistant_grep_line(path: str) -> str:
    """Return a JSONL line for an assistant message using the Grep tool."""
    entry = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "Grep",
                    "input": {"pattern": "foo", "path": path},
                }
            ],
        },
    }
    return json.dumps(entry)


# ---------------------------------------------------------------------------
# extract_user_task
# ---------------------------------------------------------------------------


class TestExtractUserTask:
    """Tests for pre_compact_hook.extract_user_task."""

    def test_empty_list_returns_unknown(self) -> None:
        assert extract_user_task([]) == "Unknown task"

    def test_single_user_text_line(self) -> None:
        lines = [_user_text_line("Implement the feature")]
        result = extract_user_task(lines)
        assert result == "Implement the feature"

    def test_returns_first_line_of_multiline_message(self) -> None:
        lines = [_user_text_line("First line\nSecond line\nThird line")]
        result = extract_user_task(lines)
        assert result == "First line"

    def test_truncates_to_200_chars(self) -> None:
        long_text = "A" * 300
        lines = [_user_text_line(long_text)]
        result = extract_user_task(lines)
        assert len(result) <= 200

    def test_text_block_content(self) -> None:
        lines = [_user_text_block_line("Fix the failing tests")]
        result = extract_user_task(lines)
        assert result == "Fix the failing tests"

    def test_only_tool_result_returns_unknown(self) -> None:
        lines = [_tool_result_line("some tool output")]
        result = extract_user_task(lines)
        assert result == "Unknown task"

    def test_skips_short_text(self) -> None:
        # text < 10 chars should be skipped
        lines = [
            _user_text_line("Hi"),
            _user_text_line("A longer task description here"),
        ]
        # Returns the last user message in reverse scan — the longer one appears last
        result = extract_user_task(lines)
        assert result == "A longer task description here"

    def test_returns_most_recent_user_message(self) -> None:
        """Scans in reverse so the last user message in the list is returned first."""
        lines = [
            _user_text_line("Earlier task from long ago"),
            _tool_result_line("tool output"),
            _user_text_line("Most recent task description"),
        ]
        result = extract_user_task(lines)
        assert result == "Most recent task description"

    def test_ignores_non_user_entries(self) -> None:
        lines = [
            _assistant_read_line("/tmp/file.txt"),
            _user_text_line("The actual user task"),
        ]
        result = extract_user_task(lines)
        assert result == "The actual user task"

    def test_malformed_json_lines_skipped(self) -> None:
        lines = [
            "not valid json",
            "{broken",
            _user_text_line("Valid task here for testing"),
        ]
        result = extract_user_task(lines)
        assert result == "Valid task here for testing"

    def test_empty_string_content_returns_unknown(self) -> None:
        lines = [_user_text_line("")]
        result = extract_user_task(lines)
        assert result == "Unknown task"

    def test_whitespace_only_content_returns_unknown(self) -> None:
        lines = [_user_text_line("   \n  ")]
        result = extract_user_task(lines)
        assert result == "Unknown task"


# ---------------------------------------------------------------------------
# extract_file_paths
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    """Tests for pre_compact_hook.extract_file_paths."""

    def test_empty_list_returns_empty(self) -> None:
        assert extract_file_paths([]) == []

    def test_read_tool_absolute_path(self) -> None:
        lines = [_assistant_read_line("/home/user/project/main.py")]
        result = extract_file_paths(lines)
        assert "/home/user/project/main.py" in result

    def test_write_tool_absolute_path(self) -> None:
        lines = [_assistant_write_line("/tmp/output.txt")]
        result = extract_file_paths(lines)
        assert "/tmp/output.txt" in result

    def test_grep_tool_path(self) -> None:
        lines = [_assistant_grep_line("/usr/local/src")]
        result = extract_file_paths(lines)
        assert "/usr/local/src" in result

    def test_non_absolute_path_skipped(self) -> None:
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "relative/path/file.py"},
                    }
                ]
            },
        }
        lines = [json.dumps(entry)]
        result = extract_file_paths(lines)
        assert result == []

    def test_deduplication(self) -> None:
        path = "/home/user/file.py"
        lines = [_assistant_read_line(path), _assistant_read_line(path)]
        result = extract_file_paths(lines)
        assert result.count(path) == 1

    def test_max_15_paths(self) -> None:
        lines = [_assistant_read_line(f"/tmp/file{i}.py") for i in range(20)]
        result = extract_file_paths(lines)
        assert len(result) <= 15

    def test_user_lines_ignored(self) -> None:
        lines = [
            _user_text_line("Please read /home/user/file.py"),
            _assistant_read_line("/home/user/actual.py"),
        ]
        result = extract_file_paths(lines)
        # Only the assistant tool_use path should appear
        assert result == ["/home/user/actual.py"]

    def test_returns_list_type(self) -> None:
        result = extract_file_paths([_assistant_read_line("/tmp/f.py")])
        assert isinstance(result, list)

    def test_malformed_json_skipped(self) -> None:
        lines = [
            "not json at all",
            _assistant_read_line("/tmp/valid.py"),
        ]
        result = extract_file_paths(lines)
        assert "/tmp/valid.py" in result

    def test_multiple_tools_in_one_message(self) -> None:
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/tmp/a.py"},
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": "/tmp/b.py", "content": ""},
                    },
                ]
            },
        }
        result = extract_file_paths([json.dumps(entry)])
        assert "/tmp/a.py" in result
        assert "/tmp/b.py" in result

    def test_unknown_tool_name_ignored(self) -> None:
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "UnknownTool",
                        "input": {"file_path": "/tmp/secret.py"},
                    }
                ]
            },
        }
        result = extract_file_paths([json.dumps(entry)])
        # UnknownTool is not in _FILE_TOOLS, so it should be ignored
        assert result == []
