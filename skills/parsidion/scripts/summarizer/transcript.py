"""Raw transcript preprocessing: tail extraction, code-fence strip.

Extracted from ``summarize_sessions.py`` (ARC-009).

``preprocess_transcript`` reads the last N lines of a Claude Code / Codex JSONL
transcript and reduces it to a cleaned ``Human: … / Assistant: …`` dialogue.
``_strip_code_fence`` unwraps a single surrounding markdown fence so the
write-gate JSON detection in ``summarize_one`` is not defeated by a fenced
decision blob.

The hierarchical chunking path (``_summarize_chunk``,
``preprocess_transcript_hierarchical``) lives here alongside the base cleaner
(QA-003). ``_summarize_chunk`` delegates to the backend prompt runner in
:mod:`summarizer.prompt`; tests monkeypatch these on this module
(``summarizer.transcript.X``), and the entry shim re-exports them so legacy
``summarize_sessions.X`` references keep resolving.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

import vault_common
from prompt_templates import render

from summarizer._state_const import (
    _DEFAULT_MAX_CLEANED_CHARS,
    _DEFAULT_TRANSCRIPT_TAIL_BYTES,
    _DEFAULT_TRANSCRIPT_TAIL_LINES,
)
from summarizer.prompt import _run_summarizer_prompt


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


def _extract_role_and_content(entry: dict[str, object]) -> tuple[str | None, object]:
    """Detect the role and content for a single transcript JSONL line.

    Tries the Claude ``message.role`` shape, the legacy top-level ``type``
    shape, and finally the Codex ``payload.type == "message"`` shape. Returns
    ``(role, content)`` where ``role`` is ``None`` when no shape matched.
    """
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

    return role, content


def _extract_text(role: str, content: object) -> str:
    """Extract visible text from a transcript line's ``content``.

    Strings pass through stripped. Lists keep only text blocks, dropping tool
    results (for user messages) and tool calls (for assistant messages). Any
    other content shape returns an empty string so the caller's truthiness
    check skips the line.
    """
    # Extract text blocks only
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
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
        return "\n".join(parts).strip()
    return ""


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

        role, content = _extract_role_and_content(entry)

        if role not in {"user", "assistant"} or not content:
            continue

        text = _extract_text(role, content)

        if not text:
            continue

        label = "Human" if role == "user" else "Assistant"
        pairs.append(f"{label}: {text}")

    cleaned = "\n\n".join(pairs)
    if max_chars is None:
        return cleaned
    return cleaned[:max_chars]


async def _summarize_chunk(
    chunk_text: str,
    chunk_num: int,
    total_chunks: int,
    model: str | None,
    vault: Path,
) -> str:
    """Summarize one chunk of a long transcript using a cheaper model.

    Args:
        chunk_text: The transcript chunk to summarize.
        chunk_num: 1-based index of this chunk.
        total_chunks: Total number of chunks.
        model: Model ID to use for summarization.
        vault: Vault path used for backend configuration and execution context.

    Returns:
        A summary string (3-5 sentences). Falls back to a truncated version of
        chunk_text on failure.
    """
    # ENH-008: chunk-summarizer prompt lives in templates/prompts/summarize-chunk.md.
    prompt = render(
        "summarize-chunk",
        chunk_num=chunk_num,
        total_chunks=total_chunks,
        chunk_text=chunk_text,
    )
    try:
        result_text = await _run_summarizer_prompt(
            prompt,
            model=model,
            model_tier="small",
            purpose="summarizer-chunk",
            timeout=vault_common.get_config("summarizer", "ai_timeout", None),
            vault=vault,
        )
    except Exception:  # noqa: BLE001
        print(
            f"  [chunk-summarizer] Unexpected error on chunk {chunk_num}/{total_chunks}:\n"
            + traceback.format_exc(),
            file=sys.stderr,
        )
        result_text = None

    if result_text:
        return result_text
    # Fallback: return truncated raw chunk
    return chunk_text[:500]


async def preprocess_transcript_hierarchical(
    transcript_path_str: str,
    tail_lines: int,
    max_cleaned_chars: int,
    cluster_model: str | None,
    vault: Path,
    tail_bytes: int | None = None,
) -> str:
    """Pre-process a transcript, using hierarchical summarization for long ones.

    For transcripts within the character limit, returns the cleaned text
    unchanged. For transcripts exceeding the limit, splits into chunks,
    summarizes each chunk with a cheaper model, and returns the combined
    chunk summaries.

    Args:
        transcript_path_str: String path to the transcript JSONL file.
        tail_lines: Number of trailing transcript lines to read.
        max_cleaned_chars: Maximum characters threshold.
        tail_bytes: Byte ceiling on the raw tail, bounding huge-line transcripts.
        cluster_model: Model ID to use for chunk summarization.
        vault: Vault path used for chunk summarization backend calls.

    Returns:
        Cleaned dialogue string, or hierarchical summary string for long sessions.
    """
    cleaned = preprocess_transcript(transcript_path_str, tail_lines, None, tail_bytes)
    if len(cleaned) <= max_cleaned_chars:
        return cleaned

    # Split into chunks at newline boundaries
    chunk_size = max_cleaned_chars // 3
    chunks: list[str] = []
    remaining = cleaned
    while remaining:
        if len(remaining) <= chunk_size:
            chunks.append(remaining)
            break
        # Find a newline near the chunk boundary to avoid mid-sentence cuts
        split_pos = remaining.rfind("\n", 0, chunk_size)
        if split_pos == -1:
            split_pos = chunk_size
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")

    total = len(chunks)
    print(
        f"  [hierarchical] Session too long ({len(cleaned)} chars), "
        f"summarizing {total} chunks..."
    )

    summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        summary = await _summarize_chunk(chunk, i + 1, total, cluster_model, vault)
        summaries.append(summary)

    header = f"[Hierarchical summary from {total} transcript segments]"
    body = "\n\n".join(f"Segment {i + 1}:\n{s}" for i, s in enumerate(summaries))
    return f"{header}\n\n{body}"
