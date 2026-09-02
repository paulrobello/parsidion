"""Prompt-AI frontmatter repair + AI-output normalization.

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import ai_backend
import vault_common
import vault_fs

from doctor._state import (
    AI_TIMEOUT,
    DEFAULT_MODEL,
    VALID_TYPES,
    Issue,
    _active_vault,
    _backup_note,
)
from doctor.links import _find_semantic_candidates, resolve_wikilink
from prompt_templates import render


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
    # BROKEN_WIKILINK belongs here: a note whose only issue was a broken link
    # got an EMPTY candidate list while still being told every link must
    # resolve, so the model invented a target — in practice a Daily note, whose
    # stem it could guess from the note's own `date:` field.
    needs_related = any(
        i.code
        in ("ORPHAN_NOTE", "MISSING_FIELD", "MISSING_FRONTMATTER", "BROKEN_WIKILINK")
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
            "  Use only the real targets listed below; every [[link]] must resolve.\n"
            "- NEVER link a daily note (a stem like '10-probello' or any note under "
            "Daily/). They match almost any topic and carry no meaning as a link.\n"
            "- If a broken link has no genuinely related replacement in the list "
            "below, DROP it rather than substituting a loosely-related note. "
            "Removing a link is recoverable; a plausible wrong one is not."
        )

    prompt = render(
        "repair-frontmatter",
        rel=str(rel),
        issue_lines=issue_lines,
        valid_types=", ".join(sorted(VALID_TYPES)),
        related_rule=related_rule,
        candidate_section=candidate_section,
        content=content,
    )

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


def _split_frontmatter_block(text: str) -> tuple[str, str] | None:
    """Split *text* into (frontmatter block incl. delimiters, body).

    Returns None when the text does not open with a ``---`` delimited block.
    """
    lines = text.splitlines(keepends=True)
    if not lines or _FM_DELIM_RE.match(lines[0].rstrip("\n")) is None:
        return None
    for i in range(1, len(lines)):
        if _FM_DELIM_RE.match(lines[i].rstrip("\n")):
            return "".join(lines[: i + 1]), "".join(lines[i + 1 :])
    return None


def splice_frontmatter_onto_original(repaired: str, original: str) -> str:
    """SEC-033(d): take only the frontmatter block from the AI repair.

    The AI repair previously wrote its whole output — frontmatter AND body —
    over the note, so any body drift the model introduced (paraphrasing,
    truncation, dropped code blocks) landed in the vault even though the
    repair only ever needed to touch frontmatter. This splices the repaired
    frontmatter onto the note's original body, byte-for-byte. When either
    side lacks a parseable frontmatter block, the validated *repaired* text
    is returned unchanged (normalization already vetted it).
    """
    repaired_split = _split_frontmatter_block(repaired)
    if repaired_split is None:
        return repaired
    original_split = _split_frontmatter_block(original)
    original_body = original_split[1] if original_split is not None else original
    return repaired_split[0] + original_body


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


# ---------------------------------------------------------------------------
# Deterministic (Python-only) frontmatter repairs — no Claude call.
#
# Two detection-only codes have a safe mechanical fix, so they are repaired
# here rather than left for a human (and not routed through the AI path,
# which is deliberately kept away from structurally broken frontmatter):
#   * NESTED_FM_KEY      — fields wrapped under a `metadata:` mapping block.
#   * SCALAR_LIST_FIELD  — tags/sources/related holding a bare scalar.
# The orchestrator calls these as a pre-pass before issue classification
# (see doctor.orchestrator._run_deterministic_frontmatter_fixes).
# ---------------------------------------------------------------------------

# A token that needs quoting inside an inline YAML list (would otherwise break
# parsing or change meaning): comma, flow markers, comment/hash, quote chars,
# or leading/trailing whitespace.
_YAML_QUOTE_RE = re.compile(r"[,\[\]{}:#\"']|^\s|\s$")


def _yaml_inline_scalar(token: str) -> str:
    """Format *token* for an inline YAML list, quoting only when necessary."""
    token = token.strip()
    if token == "" or _YAML_QUOTE_RE.search(token):
        return '"' + token.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return token


def _auto_fix_metadata_wrapper(path: Path) -> bool:
    """Flatten a single ``metadata:`` mapping wrapper to top-level keys.

    Some AI-generated notes wrap the real frontmatter under a ``metadata:``
    block. The frontmatter parser already flattens indented keys to top level,
    so this is a structural cleanup that makes the file's YAML match what the
    parser produces: the indented children are dedented to column 0 and the
    ``metadata:`` line is dropped. A child whose key already exists as a
    top-level field (or earlier in the wrapper) is dropped — the parser is
    last-wins, so the existing top-level value is authoritative.

    Returns True if the file was rewritten. Returns False (no-op) for any note
    without this specific ``metadata:``-wrapper shape, so the generic
    NESTED_FM_KEY warning still fires for other nested-key structures.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    lines = content.split("\n")
    if not lines or _FM_DELIM_RE.match(lines[0]) is None:
        return False
    closers = [i for i in range(1, len(lines)) if _FM_DELIM_RE.match(lines[i])]
    if not closers:
        return False
    closer = closers[0]
    fm_lines = lines[1:closer]

    # Locate a top-level `metadata:` key with at least one indented child.
    metadata_idx: int | None = None
    for i, ln in enumerate(fm_lines):
        if (
            ln.rstrip() == "metadata:"
            and i + 1 < len(fm_lines)
            and fm_lines[i + 1][:1].isspace()
            and fm_lines[i + 1].strip()
        ):
            metadata_idx = i
            break
    if metadata_idx is None:
        return False

    # Extent of the wrapper's indented block.
    block_end = metadata_idx + 1
    while (
        block_end < len(fm_lines)
        and fm_lines[block_end][:1].isspace()
        and fm_lines[block_end].strip()
    ):
        block_end += 1
    block = fm_lines[metadata_idx + 1 : block_end]

    # Top-level keys present outside the wrapper (authoritative for dedup).
    top_keys: set[str] = set()
    for idx, ln in enumerate(fm_lines):
        if metadata_idx <= idx < block_end:
            continue
        if _FM_KEY_RE.match(ln):
            top_keys.add(ln.split(":", 1)[0])

    # Lift children (dedent to column 0); drop keys that duplicate a top-level
    # key or an already-lifted key (and their following list items).
    lifted: list[str] = []
    emitted: set[str] = set()
    skip = False
    for ln in block:
        stripped = ln.lstrip()
        m = re.match(r"^([A-Za-z][\w-]*)\s*:", stripped)
        if m:
            k = m.group(1)
            skip = k in top_keys or k in emitted
            if skip:
                continue
            emitted.add(k)
            lifted.append(stripped)
        elif not skip:
            lifted.append(stripped)

    new_fm = fm_lines[:metadata_idx] + lifted + fm_lines[block_end:]
    new_lines = lines[:1] + new_fm + lines[closer:]
    new_content = "\n".join(new_lines)
    if new_content == content:
        return False

    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, new_content)
    return True


def _auto_fix_scalar_list_field(path: Path) -> bool:
    """Convert a scalar ``tags``/``sources``/``related`` value to a list.

    ``tags`` and ``sources``: the scalar is whitespace-split into an inline
    list. ``related``: only fixed when the scalar already contains a
    ``[[wikilink]]`` (re-wrapped as ``"[[stem]]"``); a bare-word ``related``
    is left for a human. Idempotent — a field that is already a list (or empty)
    is a no-op.

    Returns True if the file was rewritten.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    fm = vault_common.parse_frontmatter(content)
    if not fm:
        return False

    edits: dict[str, str] = {}
    for field in ("tags", "sources", "related"):
        val = fm.get(field)
        if not isinstance(val, str) or not val.strip():
            continue
        if field == "related":
            stems = re.findall(r"\[\[([^\[\]]+)\]\]", val)
            if not stems:
                continue
            items = ", ".join(f'"[[{s.split("|")[0].split("#")[0]}]]"' for s in stems)
            edits[field] = f"[{items}]"
        else:
            items = ", ".join(_yaml_inline_scalar(t) for t in val.split())
            if not items:
                continue
            edits[field] = f"[{items}]"
    if not edits:
        return False

    lines = content.split("\n")
    if not lines or _FM_DELIM_RE.match(lines[0]) is None:
        return False
    closers = [i for i in range(1, len(lines)) if _FM_DELIM_RE.match(lines[i])]
    if not closers:
        return False
    closer = closers[0]

    remaining = dict(edits)
    for i in range(1, closer):
        if not remaining:
            break
        m = re.match(r"^([A-Za-z][\w-]*)\s*:", lines[i])
        if m and m.group(1) in remaining:
            lines[i] = f"{m.group(1)}: {remaining.pop(m.group(1))}"
    if remaining:
        return False  # a targeted field was not a top-level line (e.g. still nested)

    new_content = "\n".join(lines)
    if new_content == content:
        return False
    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, new_content)
    return True


def _auto_fix_stray_list_items(path: Path) -> bool:
    """Normalize fields having stray indented list items after an inline list.

    Preserves any [[wikilinks]] inside stray continuations of `related:` and
    merges them into the inline list, while dropping invalid syntax.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = content.split("\n")
    if not lines or _FM_DELIM_RE.match(lines[0]) is None:
        return False
    closers = [i for i in range(1, len(lines)) if _FM_DELIM_RE.match(lines[i])]
    if not closers:
        return False
    closer = closers[0]

    fm_lines = lines[1:closer]
    new_fm_lines: list[str] = []
    i = 0
    changed = False

    while i < len(fm_lines):
        line = fm_lines[i]
        if ":" in line and not line[:1].isspace() and _FM_KEY_RE.match(line):
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip()
            # Check if this line is an inline list
            if val.startswith("[") and val.count("[") == val.count("]"):
                j = i + 1
                continuation_lines: list[str] = []
                while (
                    j < len(fm_lines)
                    and fm_lines[j][:1].isspace()
                    and fm_lines[j].strip().startswith("- ")
                ):
                    continuation_lines.append(fm_lines[j].strip())
                    j += 1
                if continuation_lines:
                    changed = True
                    if key == "related":
                        stems = re.findall(r"\[\[([^\[\]]+)\]\]", val)
                        for cln in continuation_lines:
                            stems.extend(re.findall(r"\[\[([^\[\]]+)\]\]", cln))
                        # Deduplicate
                        seen: set[str] = set()
                        deduped: list[str] = []
                        for s in stems:
                            clean_s = s.split("|")[0].split("#")[0]
                            if clean_s not in seen:
                                seen.add(clean_s)
                                deduped.append(clean_s)
                        items = ", ".join(f'"[[{s}]]"' for s in deduped)
                        new_fm_lines.append(f"{key}: [{items}]")
                    else:
                        new_fm_lines.append(line)
                    i = j
                    continue
        new_fm_lines.append(line)
        i += 1

    if not changed:
        return False

    new_lines = lines[:1] + new_fm_lines + lines[closer:]
    new_content = "\n".join(new_lines)
    if new_content == content:
        return False
    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, new_content)
    return True
