"""Prompt-AI frontmatter repair + AI-output normalization.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import ai_backend
import vault_common

from doctor._state import AI_TIMEOUT, DEFAULT_MODEL, VALID_TYPES, Issue, _active_vault
from doctor.links import _find_semantic_candidates, resolve_wikilink


def repair_note(
    path: Path,
    issues: list[Issue],
    model: str | None = DEFAULT_MODEL,
    timeout: int = AI_TIMEOUT,
    vault_path: Path | None = None,
) -> tuple[str | None, str]:
    """Call the configured prompt AI backend to fix *issues* in *path*.

    Returns (fixed_content_or_None, status) where status is one of
    "fixed", "failed", or "timeout".
    """
    if vault_path is None:
        vault_path = _active_vault()
    content = path.read_text(encoding="utf-8")
    rel = path.relative_to(vault_path)
    issue_lines = "\n".join(
        f"  - [{i.severity.upper()}] {i.code}: {i.message}" for i in issues
    )

    # Daily notes are exempt from the confidence/related requirements, so do
    # not force the model to fabricate a 'related' link for them.
    is_daily = _note_is_daily(rel, content)

    # For any non-daily note that must produce/fix a 'related' field, surface
    # real semantically-similar notes so the model picks actual wikilinks
    # instead of inventing targets that don't resolve to anything.
    needs_related = any(
        i.code in ("ORPHAN_NOTE", "MISSING_FIELD", "MISSING_FRONTMATTER")
        for i in issues
    )
    candidates: list[str] = (
        _find_semantic_candidates(path) if (not is_daily and needs_related) else []
    )
    candidate_section = ""
    if candidates:
        links = ", ".join(f"[[{s}]]" for s in candidates)
        candidate_section = (
            f"\n\nReal vault notes related to this one (choose the 'related' "
            f"links ONLY from this list — do NOT invent other targets):\n{links}"
        )

    if is_daily:
        related_rule = "- Daily notes: set 'related: []' (they are exempt from linking)"
    else:
        related_rule = (
            "- 'related' MUST be a single-line inline YAML array of [[wikilinks]], "
            "exactly like:\n"
            '      related: ["[[some-note]]", "[[another-note]]"]\n'
            "  Use only the real targets listed below; every [[link]] must resolve."
        )

    prompt = f"""You are a vault note repair tool. Fix ONLY the listed issues in this Obsidian markdown note.
Do NOT rewrite, summarise, or add content beyond what is needed to resolve each issue.
Return ONLY the corrected note as raw markdown. No explanation, no code fences, and
do NOT echo the ---BEGIN--- / ---END--- markers shown below.

File: {rel}

Issues to fix:
{issue_lines}

Rules:
- Valid values for 'type': {", ".join(sorted(VALID_TYPES))}
- Valid values for 'confidence': high | medium | low
- 'date' must be YYYY-MM-DD
- Emit exactly ONE YAML frontmatter block: a '---' line, the fields, then a '---' line.
- Every non-daily note needs: date, type, confidence, related in its frontmatter
- 'sources' should be [] if unknown
{related_rule}{candidate_section}

Current note:
---BEGIN---
{content}
---END---"""

    try:
        output = ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier="small",
            timeout=timeout,
            purpose="vault-doctor",
            vault=vault_path,
            raise_on_timeout=True,
        )
    except ai_backend.AiBackendTimeout:
        return None, "timeout"
    if output:
        output = output.strip()
        # Strip accidental markdown fences if the backend added them
        output = re.sub(r"^```[a-z]*\n?", "", output)
        output = re.sub(r"\n?```$", "", output)
        # Strip echoed BEGIN/END markers from the prompt wrapper
        output = re.sub(r"^---BEGIN---\s*\n?", "", output)
        output = re.sub(r"\n?---END---\s*$", "", output)
        if output:
            return output, "fixed"
    return None, "failed"


# ---------------------------------------------------------------------------
# AI-output normalization (defence against malformed frontmatter)
# ---------------------------------------------------------------------------

# A bare YAML document delimiter (exactly three dashes, optional trailing space).
_FM_DELIM_RE = re.compile(r"^---\s*$")
# A leaked prompt-wrapper / fence marker the small model sometimes echoes back:
# ---BEGIN---, ---END---, ---yaml, ---YAML (any case). Bare '---' is NOT matched.
_FM_LEAKED_MARKER_RE = re.compile(r"^---(?:BEGIN|END|YAML)-*\s*$", re.IGNORECASE)
# A frontmatter field line: 'key: value' or 'key:'.
_FM_KEY_RE = re.compile(r"^[A-Za-z][\w-]*\s*:")
# The 'related:' line (matches regardless of the value that follows).
_FM_RELATED_RE = re.compile(r"^related:\s*(.*)$")


def _note_is_daily(rel: Path, content: str) -> bool:
    """A note is daily if it lives under ``Daily/`` or its frontmatter type is daily."""
    if rel.parts and rel.parts[0] == "Daily":
        return True
    fm = vault_common.parse_frontmatter(content)
    return bool(fm and fm.get("type") == "daily")


def _frontmatter_stems(raw: str, note_map: dict[str, list[Path]] | None) -> list[str]:
    """Extract ``[[wikilink]]`` stems from an arbitrary ``related`` value.

    Tolerates the malformed shapes the small model emits — double-nested
    brackets (``[["[[x]]"]]``), missing outer brackets, surrounding quotes, and
    markdown links (``[label](url)``) — by pulling the innermost ``[[...]]``
    spans. Stems are de-aliased, de-duplicated (first-seen order), and filtered
    to those that resolve via *note_map* (when provided).
    """
    stems: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\[\[([^\[\]]+)\]\]", raw):
        stem = m.group(1).split("|")[0].split("#")[0].strip()
        if not stem or stem.lower() in seen:
            continue
        if note_map is not None and not resolve_wikilink(stem, note_map):
            continue
        seen.add(stem.lower())
        stems.append(stem)
    return stems


def _normalize_repaired_note(
    content: str,
    note_map: dict[str, list[Path]] | None,
    is_daily: bool,
) -> str | None:
    """Validate and normalize an AI-repaired note before it is written to disk.

    Guards against the small model emitting malformed frontmatter:

    * a missing closing ``---`` delimiter
    * leaked ``---BEGIN---`` / ``---END---`` / ``---yaml`` markers or code fences
    * a ``related`` value that is not a flat inline array of ``[[wikilinks]]``
    * fabricated ``[[wikilink]]`` targets that resolve to no vault note

    Returns the cleaned note (frontmatter + body), or ``None`` if the output
    cannot be made valid — the caller then treats this as a failed repair so
    nothing is written and the note is retried on the next run instead of being
    corrupted.
    """
    text = content.strip()
    # 1. Drop a leading/trailing code fence the model may have wrapped the note in.
    text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    # 2. Drop leaked wrapper markers (but never a bare '---' delimiter).
    lines = [ln for ln in text.split("\n") if not _FM_LEAKED_MARKER_RE.match(ln)]
    if not lines:
        return None

    # 3. Locate the frontmatter block: first bare '---', next bare '---'.
    delim_idx = [i for i, ln in enumerate(lines) if _FM_DELIM_RE.match(ln)]
    if not delim_idx:
        return None  # no frontmatter at all — reject
    opener = delim_idx[0]
    if len(delim_idx) < 2:
        # Missing closer: insert one where the body begins (first line that is
        # neither a key:value field, a block-sequence entry, nor blank).
        closer = opener + 1
        while closer < len(lines) and (
            lines[closer].strip() == ""
            or _FM_KEY_RE.match(lines[closer])
            or lines[closer].startswith(" ")
            or lines[closer].startswith("-")
        ):
            closer += 1
        lines.insert(closer, "---")
    else:
        closer = delim_idx[1]

    fm_lines = lines[opener + 1 : closer]
    body_lines = lines[closer + 1 :]

    # Reject stacked/duplicate frontmatter blocks: if the body itself opens
    # with '---' or a key:value line, the model emitted two blocks — too risky
    # to disentangle, so retry rather than guess.
    for ln in body_lines:
        s = ln.strip()
        if s == "":
            continue
        if s == "---" or _FM_KEY_RE.match(s):
            return None
        break

    # 4. Rebuild the 'related:' line as a clean inline array of resolving links.
    new_fm: list[str] = []
    related_value = ""
    related_at: int | None = None
    for i, ln in enumerate(fm_lines):
        m = _FM_RELATED_RE.match(ln)
        if related_at is None and m:
            related_at = i
            related_value = m.group(1)
        else:
            new_fm.append(ln)

    stems = _frontmatter_stems(related_value, note_map)
    if not stems:
        if not is_daily:
            return None  # non-daily needs at least one real link
        new_related = "related: []"
    else:
        new_related = "related: [" + ", ".join(f'"[[{s}]]"' for s in stems) + "]"

    if related_at is not None:
        new_fm.insert(related_at, new_related)
    else:
        new_fm.append(new_related)

    return "\n".join(["---", *new_fm, "---", *body_lines])
