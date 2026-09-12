"""Adaptive tail-window tests for ``summarizer.transcript.preprocess_transcript``.

A transcript whose final ``tail_bytes`` window holds only telemetry records
(codex ``event_msg``/``token_count``, claude ``attachment``) preprocesses to
zero dialogue pairs, which the pipeline misreports as ``transcript_read``
("could not read transcript"). The read must grow its window geometrically —
up to a hard cap — before giving up, while tails that already contain dialogue
keep the configured window.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import core.transcript_reader  # noqa: E402

# The summarizer-section schema default (256 KiB) — the window that fails on
# telemetry-dense tails.
_BASE_WINDOW = 262_144
_MAX_LINE_BYTES = 262_144


def _dialogue_line(text: str, role: str = "assistant") -> str:
    return (
        json.dumps({"type": role, "content": [{"type": "text", "text": text}]}) + "\n"
    )


def _telemetry_line(size: int) -> str:
    blob = "x" * size
    return (
        json.dumps(
            {
                "type": "event_msg",
                "payload": {"type": "token_count", "info": {"blob": blob}},
            }
        )
        + "\n"
    )


def _write_transcript(path: Path, lines: list[str]) -> None:
    path.write_text("".join(lines), encoding="utf-8")


@pytest.fixture()
def preprocess(monkeypatch: pytest.MonkeyPatch):
    """``preprocess_transcript`` with a per-test anyio stub.

    Lazy import per the suite convention: ``summarizer.transcript`` pulls in
    ``summarizer.prompt`` → ``anyio``, and a stub left in ``sys.modules`` at
    collection time would defeat ``pytest.importorskip("anyio")`` in
    test_summarize_sessions' real-task-group test.
    """
    monkeypatch.setitem(
        sys.modules,
        "anyio",
        types.SimpleNamespace(
            Semaphore=object,
            to_thread=types.SimpleNamespace(run_sync=lambda func, *args: func(*args)),
        ),
    )
    import summarizer.transcript

    return summarizer.transcript.preprocess_transcript


@pytest.fixture()
def _spy_read_tail(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, int]]:
    """Record every (max_bytes, max_line_bytes) read_tail call, pass through."""
    calls: list[tuple[int, int]] = []
    real = core.transcript_reader.read_tail

    def spy(path: Path, *, tail_lines: int, max_bytes: int, max_line_bytes: int):
        calls.append((max_bytes, max_line_bytes))
        return real(
            path,
            tail_lines=tail_lines,
            max_bytes=max_bytes,
            max_line_bytes=max_line_bytes,
        )

    monkeypatch.setattr(core.transcript_reader, "read_tail", spy)
    return calls


class TestAdaptiveTailWindow:
    def test_telemetry_dense_tail_recovers_via_growth(
        self,
        tmp_path: Path,
        preprocess,
        _spy_read_tail: list[tuple[int, int]],
    ) -> None:
        path = tmp_path / "rollout.jsonl"
        _write_transcript(
            path,
            [
                _dialogue_line("Refactored the persist pipeline for chunked writes."),
                _dialogue_line("All eight replacement jobs completed cleanly."),
            ]
            + [_telemetry_line(4096) for _ in range(200)],
        )

        cleaned = preprocess(str(path), 400, None, _BASE_WINDOW, vault=tmp_path)

        assert "replacement jobs" in cleaned
        # One growth step from 256 KiB reaches the dialogue.
        assert _spy_read_tail[0] == (_BASE_WINDOW, _MAX_LINE_BYTES)
        assert _spy_read_tail[1][0] > _BASE_WINDOW

    def test_growth_stops_at_hard_cap(
        self,
        tmp_path: Path,
        preprocess,
        _spy_read_tail: list[tuple[int, int]],
    ) -> None:
        path = tmp_path / "huge.jsonl"
        # ~10 MB of telemetry: even the 8 MiB cap window cannot reach dialogue.
        _write_transcript(
            path,
            [
                _dialogue_line("Buried far before the telemetry wall."),
            ]
            + [_telemetry_line(40_000) for _ in range(250)],
        )

        cleaned = preprocess(str(path), 400, None, _BASE_WINDOW, vault=tmp_path)

        assert cleaned == ""
        windows = [max_bytes for max_bytes, _ in _spy_read_tail]
        assert windows == [262_144, 1_048_576, 4_194_304, 8_388_608]
        assert all(line_bytes == _MAX_LINE_BYTES for _, line_bytes in _spy_read_tail)

    def test_dialogue_in_window_never_grows(
        self,
        tmp_path: Path,
        preprocess,
        _spy_read_tail: list[tuple[int, int]],
    ) -> None:
        path = tmp_path / "short.jsonl"
        _write_transcript(
            path,
            [
                _dialogue_line("Straightforward session, dialogue right in the tail."),
            ]
            + [_telemetry_line(1024) for _ in range(20)],
        )

        cleaned = preprocess(str(path), 400, None, _BASE_WINDOW, vault=tmp_path)

        assert "Straightforward session" in cleaned
        assert len(_spy_read_tail) == 1
        assert _spy_read_tail[0] == (_BASE_WINDOW, _MAX_LINE_BYTES)
