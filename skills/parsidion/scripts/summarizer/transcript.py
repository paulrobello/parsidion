"""Raw transcript preprocessing: tail extraction, code-fence strip.

Extracted from ``summarize_sessions.py`` (ARC-009).

``preprocess_transcript`` reads the last N lines of a Claude Code / Codex JSONL
transcript and reduces it to a cleaned ``Human: … / Assistant: …`` dialogue.
``_strip_code_fence`` unwraps a single surrounding markdown fence so the
write-gate JSON detection in ``summarize_one`` is not defeated by a fenced
decision blob.

The hierarchical chunking path (``_summarize_chunk``,
``preprocess_transcript_hierarchical``) remains in the entry shim because tests
monkeypatch those functions on ``summarize_sessions``.
"""

from __future__ import annotations

import json
from pathlib import Path

import vault_common

from summarizer._state_const import (
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
)


def _strip_code_fence(text: str) -> str:
    """Strip a single surrounding markdown code fence, if present.

    The summarizer backend occasionally wraps a JSON write-gate decision in a
    ```` ```json ```` fence. Without stripping, the ``startswith("{")`` check
    misses it and a "skip"/"merge" decision falls through to ``write_note``,
    which fails frontmatter validation and reports a false "failed" result.
    Only one outer fence is removed so a genuinely fenced note body is intact.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    newline = stripped.find("\n")
    if newline == -1:
        return stripped
    inner = stripped[newline + 1 :]
    end = inner.rfind("```")
    if end != -1:
        inner = inner[:end]
    return inner.strip()


def preprocess_transcript(
    transcript_path_str: str,
    tail_lines: int = _DEFAULT_TRANSCRIPT_TAIL_LINES,
    max_chars: int | None = _DEFAULT_MAX_CLEANED_CHARS,
    tail_bytes: int | None = _DEFAULT_TRANSCRIPT_TAIL_BYTES,
) -> str:
    """Pre-process a transcript JSONL file into a cleaned human/assistant dialogue.

    Reads last N lines, keeps only human and assistant text blocks,
    strips tool calls and tool results, and optionally truncates to a character limit.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_chars: Maximum output characters, or ``None`` to return all cleaned text.
        tail_bytes: Byte ceiling on the raw tail (see ``read_last_n_lines``); bounds
            transcripts with few-but-huge lines before cleaning.

    Returns:
        Cleaned dialogue string, truncated to *max_chars* when provided.
    """
    transcript_path = Path(transcript_path_str)
    if not transcript_path.is_file():
        return ""

    try:
        tail = vault_common.read_last_n_lines(transcript_path, tail_lines, tail_bytes)
    except OSError:
        return ""

    pairs: list[str] = []

    for raw_line in tail:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        role: str | None = None
        content: object = None

        message = entry.get("message")
        if isinstance(message, dict):
            role_raw = message.get("role")
            if isinstance(role_raw, str):
                role = role_raw
            content = message.get("content")

        if role is None:
            msg_type = entry.get("type")
            if isinstance(msg_type, str) and msg_type in {"user", "assistant"}:
                role = msg_type
                content = entry.get("content")

        # Codex format: type="response_item", payload.type="message",
        # payload.role="user"/"assistant", payload.content=[{type:"input_text"/"output_text"}]
        if role is None:
            payload = entry.get("payload")
            if isinstance(payload, dict) and payload.get("type") == "message":
                role_raw = payload.get("role")
                if isinstance(role_raw, str) and role_raw in {"user", "assistant"}:
                    role = role_raw
                    content = payload.get("content")

        if role not in {"user", "assistant"} or not content:
            continue

        # Extract text blocks only
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                # For user messages, skip tool result blocks.
                # For assistant messages, skip tool call/use blocks.
                block_type = block.get("type", "")
                if role == "user" and block_type in {"tool_result", "toolResult"}:
                    continue
                if role == "assistant" and block_type in {"tool_use", "toolCall"}:
                    continue
                if block_type in {"text", "input_text", "output_text"}:
                    t = block.get("text", "")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            text = "\n".join(parts).strip()
        else:
            continue

        if not text:
            continue

        label = "Human" if role == "user" else "Assistant"
        pairs.append(f"{label}: {text}")

    cleaned = "\n\n".join(pairs)
    if max_chars is None:
        return cleaned
    return cleaned[:max_chars]
