"""ENH-018: the unified byte-bounded transcript reader.

Pins:

* A multi-MB single JSONL line no longer collapses the tail: records on
  both sides of it survive, and the oversized record itself is kept with
  its long string fields truncated.
* ``max_bytes`` smaller than the last line still returns that record.
* ``require_allowed=True`` rejects paths outside the transcript roots.
* ``agent_adapter._read_transcript_tail`` routes through ``read_tail``
  (spy via the core module attribute — the function imports lazily).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core import transcript_reader  # noqa: E402
from core.transcript_reader import (  # noqa: E402
    TranscriptPathError,
    read_tail,
    truncate_oversized_fields,
)


def _entry(role: str, text: str) -> str:
    return json.dumps(
        {
            "type": role,
            "message": {"role": role, "content": [{"type": "text", "text": text}]},
        }
    )


class TestHugeLineChunking:
    def test_records_survive_both_sides_of_a_3mb_line(self, tmp_path: Path) -> None:
        big = "x" * (3 * 1024 * 1024)
        transcript = tmp_path / "huge.jsonl"
        transcript.write_text(
            _entry("user", "first message")
            + "\n"
            + _entry("assistant", big)
            + "\n"
            + _entry("user", "last message")
            + "\n",
            encoding="utf-8",
        )
        tail = read_tail(transcript, tail_lines=200, max_bytes=5 * 1024 * 1024)
        texts = [rec["message"]["content"][0]["text"] for rec in tail.records]
        # The record containing the 3 MB line is KEPT, field-truncated…
        assert any(str(t).startswith("<truncated") for t in texts)
        # …and the records on both sides of it survive.
        assert "first message" in texts
        assert "last message" in texts
        assert tail.oversized_lines == 1

    def test_max_bytes_smaller_than_last_line_still_returns_it(
        self, tmp_path: Path
    ) -> None:
        big = "y" * (300 * 1024)
        transcript = tmp_path / "last-big.jsonl"
        transcript.write_text(
            _entry("user", "older") + "\n" + _entry("assistant", big) + "\n",
            encoding="utf-8",
        )
        tail = read_tail(transcript, tail_lines=200, max_bytes=64 * 1024)
        # The window covers only (part of) the last line; it is kept,
        # truncated, rather than dropped as an empty tail.
        assert tail.records, "expected at least the truncated last record"
        assert tail.truncated is True

    def test_truncate_oversized_fields_walks_structures(self) -> None:
        big = "z" * (100 * 1024)
        value = {"a": big, "b": [{"c": big}, "short"], "d": 7}
        shrunk = truncate_oversized_fields(value)
        assert shrunk["a"].startswith("<truncated")  # type: ignore[index]
        inner = shrunk["b"][0]["c"]  # type: ignore[index]
        assert str(inner).startswith("<truncated")
        assert shrunk["b"][1] == "short"  # type: ignore[index]
        assert shrunk["d"] == 7  # type: ignore[index]


class TestAllowlist:
    def test_path_outside_allowlist_raises(self, tmp_path: Path) -> None:
        rogue = tmp_path / "not-a-transcript.jsonl"
        rogue.write_text(_entry("user", "hi") + "\n", encoding="utf-8")
        with pytest.raises(TranscriptPathError):
            read_tail(
                rogue,
                tail_lines=10,
                max_bytes=1024,
                require_allowed=True,
            )

    def test_allowlist_off_by_default(self, tmp_path: Path) -> None:
        plain = tmp_path / "anywhere.jsonl"
        plain.write_text(_entry("user", "hi") + "\n", encoding="utf-8")
        tail = read_tail(plain, tail_lines=10, max_bytes=1024)
        assert len(tail.records) == 1

    def test_missing_file_yields_empty_tail(self, tmp_path: Path) -> None:
        tail = read_tail(tmp_path / "gone.jsonl", tail_lines=10, max_bytes=1024)
        assert tail.records == []
        assert tail.lines == []


class TestAdapterRouting:
    def test_agent_adapter_tail_routes_through_read_tail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import agent_adapter

        calls: list[Path] = []

        def spy_read_tail(path: Path, **kwargs: object) -> object:
            calls.append(path)
            return transcript_reader.TranscriptTail(lines=[_entry("user", "routed")])

        monkeypatch.setattr(transcript_reader, "read_tail", spy_read_tail)
        transcript = tmp_path / "session.jsonl"
        transcript.write_text(_entry("user", "routed") + "\n", encoding="utf-8")

        lines = agent_adapter._read_transcript_tail(transcript, 200)

        assert calls == [transcript]
        assert json.loads(lines[0])["message"]["content"][0]["text"] == "routed"
