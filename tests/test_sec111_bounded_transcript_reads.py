"""SEC-111 tests — transcript reads bounded by bytes, not just lines.

The byte-bounded ``read_last_n_lines`` from ``summarize_sessions.py:280``
was extracted to ``vault_fs`` and shared across the four transcript
readers. Before SEC-111 the helper still iterated the entire file via
``deque(maxlen=n)``; a single newline-free 50 MB line dragged the whole
file into memory before the byte cap could trim. The fix seeks to
``file_size - max_bytes`` before iterating so the iteration window is
already bounded.

Subagent-stop hook also had ``f.readlines()`` with no cap at all under
the false comment "subagent sessions are short". Now it uses the shared
byte-bounded reader.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from vault_fs import read_last_n_lines  # noqa: E402


class TestReadLastNLinesByteBounding:
    """``read_last_n_lines`` bounds memory and return size for huge lines."""

    def test_small_file_returns_all_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("a\nb\nc\n")
        assert read_last_n_lines(f, n=10) == ["a\n", "b\n", "c\n"]

    def test_max_lines_cap(self, tmp_path: Path) -> None:
        f = tmp_path / "lines.txt"
        f.write_text("l1\nl2\nl3\nl4\nl5\n")
        assert read_last_n_lines(f, n=2) == ["l4\n", "l5\n"]

    def test_max_bytes_drops_oldest_lines(self, tmp_path: Path) -> None:
        f = tmp_path / "lined.txt"
        f.write_text("aaaa\nbbbb\ncccc\ndd\nee\n")
        # bytes: 5+5+5+3+3 = 21; cap at 9 → keep last 2 lines (3+3=6).
        out = read_last_n_lines(f, n=10, max_bytes=9)
        assert out == ["dd\n", "ee\n"]

    def test_max_bytes_keeps_most_recent_when_window_has_no_newline(
        self, tmp_path: Path
    ) -> None:
        # Cap below the only line's size and the line has no newline at
        # all: the whole trailing window is returned as one (truncated)
        # most-recent line. Mirrors the existing
        # ``test_read_last_n_lines_byte_bound_keeps_most_recent`` contract:
        # never return empty when the file has content.
        f = tmp_path / "single_line.txt"
        f.write_text("y" * 5000 + "\n")
        out = read_last_n_lines(f, n=10, max_bytes=10)
        assert len(out) == 1
        # The most-recent line is at most the byte budget (allowing the
        # most-recent-line keep-rule to overflow up to one line).
        assert sum(len(ln.encode("utf-8", "replace")) for ln in out) <= 10

    def test_single_huge_newline_free_line_is_bounded(self, tmp_path: Path) -> None:
        """SEC-111 regression: a single 50 MB newline-free line must not
        drag the whole file into memory before the byte cap is enforced.
        """
        huge_size = 50 * 1024 * 1024  # 50 MB
        max_bytes = 64 * 1024  # 64 KB cap
        f = tmp_path / "huge.txt"
        # Write a 50 MB file as one giant line (no newlines).
        with open(f, "wb") as fh:
            fh.write(b"X" * huge_size)
        out = read_last_n_lines(f, n=10_000, max_bytes=max_bytes)
        # Bounded return: at most max_bytes (allow small overshoot from
        # the most-recent-line keep-rule; should never exceed a few MB).
        total_bytes = sum(len(ln.encode("utf-8", "replace")) for ln in out)
        # Allow the most-recent line to overflow the budget, but only by
        # the size of one read — no more than ~max_bytes + a few MB at
        # most. The single-line case is the worst case.
        assert total_bytes <= max_bytes + 5 * 1024 * 1024
        # And we got *something* back — not zero.
        assert len(out) >= 1

    def test_file_smaller_than_max_bytes_uses_full_file(self, tmp_path: Path) -> None:
        f = tmp_path / "small.txt"
        f.write_text("hello\nworld\n")
        out = read_last_n_lines(f, n=10, max_bytes=10_000)
        assert out == ["hello\n", "world\n"]

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.txt"
        f.write_text("")
        assert read_last_n_lines(f, n=10, max_bytes=100) == []

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        f = tmp_path / "nope.txt"
        assert read_last_n_lines(f, n=10, max_bytes=100) == []

    def test_file_with_trailing_newlines_only(self, tmp_path: Path) -> None:
        f = tmp_path / "nl.txt"
        f.write_text("\n\n\n")
        out = read_last_n_lines(f, n=10, max_bytes=100)
        # Three newlines are 3 bytes total — well within the cap.
        assert len(out) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
