"""Shared recall-context pipeline for the two retrieval hooks.

Extracted from ``user_prompt_submit_hook`` and ``pre_tool_use_hook``
(ARC-202): both hooks format matched notes into the same bounded,
untrusted-framed context body, read their config section through the same
defaulting/clamping logic, and gate parsight behind the same negative-cache
stamp dance. This module owns those bodies; the hooks keep only their
entry-point wiring (payload parsing, per-file caching, stage timing).

Stdlib-only (ARC-004): imported in-process by both retrieval hooks and held
to the same enforcement contract as ``core/*``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from parsidion.core.vault_config import clamp_timeout, load_typed_config
from parsidion.core.vault_index import read_note_summary
from parsidion.session_start.context import UNTRUSTED_PREAMBLE


def _note_tags(note: dict[str, object]) -> str:
    """Space-joined string tags; non-list/non-str shapes collapse to ""."""
    raw = note.get("tags")
    if not isinstance(raw, list):
        return ""
    return " ".join(t for t in raw if isinstance(t, str))


def _excerpt(note: dict[str, object], per_note_chars: int) -> str:
    """One-line excerpt: result ``summary`` if truthy, else read_note_summary."""
    summary = str(note.get("summary") or "").strip()
    if not summary:
        raw_path = str(note.get("path") or "")
        if raw_path:
            try:
                summary = read_note_summary(Path(raw_path)).strip()
            except Exception:  # noqa: BLE001
                summary = ""
    summary = " ".join(summary.split())  # collapse to a single line
    if len(summary) > per_note_chars:
        summary = summary[: per_note_chars - 1].rstrip() + "…"
    return summary


def _build_context(
    notes: list[dict[str, object]],
    recall_subject: str,
    settings: dict[str, Any],
    rules: list[dict[str, object]] | None = None,
    note_tags: Callable[[dict[str, object]], str] = _note_tags,
) -> str:
    """Format matched notes into the bounded, untrusted-framed context body.

    *recall_subject* names what the notes are relevant to (``"this prompt"``
    on the prompt-submit path, the file basename on the pre-tool-use path).
    *note_tags* is the caller's tag-shape handler — the two hooks accept
    different tag shapes in their notes, so the display keeps per-caller
    semantics as a parameter, not a fork. Triggered ``type: rule`` notes
    lead the body when present — they are directives, not recall — and
    share the same char budget. Each rule line names the triggers that
    fired.
    """
    per_note_chars = int(settings["per_note_chars"])
    max_chars = int(settings["max_chars"])
    preamble = UNTRUSTED_PREAMBLE + "<content>\n"
    suffix = "\n</content>\n"
    available_body_chars = max_chars - len(preamble) - len(suffix)
    if available_body_chars < 1:
        return ""
    lines: list[str] = []
    if rules:
        lines.append(f"Vault rules — {len(rules)} rule(s) triggered:")
        for rule in rules:
            title = str(rule.get("title") or rule.get("stem") or "untitled")
            folder = str(rule.get("folder") or "")
            stem = str(rule.get("stem") or "")
            loc = f"{folder}/{stem}" if folder else stem
            matched = rule.get("matched")
            matched_list = matched if isinstance(matched, list) else []
            trig = ", ".join(str(t) for t in matched_list)
            lines.append(f"- **{title}** [{loc}] (triggered: {trig})")
            lines.append(f"  {_excerpt(rule, per_note_chars)}")
        lines.append("")
    if notes:
        lines.append(
            f"Vault recall — {len(notes)} note(s) relevant to {recall_subject}:"
        )
    for note in notes:
        title = str(note.get("title") or note.get("stem") or "untitled")
        folder = str(note.get("folder") or "")
        stem = str(note.get("stem") or "")
        loc = f"{folder}/{stem}" if folder else stem
        tags = ", ".join(t for t in note_tags(note).split() if t)
        lines.append(f"- **{title}** [{loc}] ({tags})")
        lines.append(f"  {_excerpt(note, per_note_chars)}")
    body = "\n".join(lines)
    available_body_chars = max_chars - len(preamble) - len(suffix)
    if len(body) > available_body_chars:
        body = body[:available_body_chars]
    return preamble + body + suffix


def _coerce_scalar(value: object, fallback: Any) -> Any:
    """Snap a config scalar to *fallback*'s type; malformed values snap too.

    The typed schema declares bool/int/float per key (ARC-007) but values
    pass through the parser untyped, so a mis-typed config value used to
    surface mid-stage inside a swallowing except (QA-008) and silently
    degrade recall. Missing config keys arrive as ``None`` (absent keys
    are ``None`` on the section dataclass) and snap to the fallback as
    well, replacing the old ``getattr(..., default)`` per-key fallback.
    """
    if isinstance(value, bool):
        return value if isinstance(fallback, bool) else fallback
    if isinstance(fallback, bool):
        return fallback
    if isinstance(fallback, int):
        return value if isinstance(value, int) else fallback
    if isinstance(fallback, float):
        return float(value) if isinstance(value, (int, float)) else fallback
    if isinstance(fallback, str):
        return value if isinstance(value, str) else fallback
    return value


def load_hook_settings(
    vault: Path,
    section: str,
    defaults: dict[str, Any],
    timeout_hi: float,
) -> dict[str, Any]:
    """Read the *section* config section, defaulting and typing per key.

    Tolerates the section being absent (or config load failing) so the
    hook never blocks on configuration problems. Each value is snapped to
    the fallback default's type (see :func:`_coerce_scalar`) — callers
    read ``settings["top_k"]`` as an ``int`` directly, no per-use
    ``int()``/``float()`` re-conversion. ``recall_timeout_s`` is clamped
    to ``[1.0, timeout_hi]`` regardless of source: NaN/negative would
    reach ``communicate(timeout=...)`` as "no timeout" (SEC-024 shape),
    and a value above the ceiling recreates the 10s host-kill bug.
    """
    settings: dict[str, Any] = dict(defaults)
    try:
        sec = getattr(load_typed_config(vault=vault), section, None)
    except Exception:  # noqa: BLE001
        return settings
    if sec is None:
        return settings
    for key, default in defaults.items():
        settings[key] = _coerce_scalar(getattr(sec, key, None), default)
    settings["recall_timeout_s"] = clamp_timeout(
        settings["recall_timeout_s"],
        default=defaults["recall_timeout_s"],
        lo=1.0,
        hi=timeout_hi,
    )
    return settings


def stamp_fresh(stamp: Path, max_age_s: int) -> bool:
    """True when *stamp* exists and is younger than *max_age_s* seconds."""
    try:
        return (time.time() - stamp.stat().st_mtime) < max_age_s
    except OSError:
        return False


def set_stamp(stamp: Path, present: bool) -> None:
    """Create (*present*) or clear (*not present*) *stamp*, best-effort.

    The negative-cache dance shared by the availability probe and the
    budget-burning search cooldown: a failure must never block the hook.
    """
    try:
        if present:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        else:
            stamp.unlink(missing_ok=True)
    except OSError:
        pass
