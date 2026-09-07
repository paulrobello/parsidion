"""Single source of truth for the vault note schema (ENH-008, Step 2).

The note-type enum, the type→folder routing, the required frontmatter fields,
and the tag rules used across the summarizer, ``vault_doctor``, and
``vault_new``. Before this module existed, the type set was restated in two
vocabularies — ``summarizer._state_const._VALID_NOTE_TYPES`` and
``doctor._state._VALID_TYPES`` — kept in sync only by a parity test. That
duplicated definition is exactly how the original ARC-010 bug happened: the
summarizer's enum silently dropped ``knowledge``, so model responses with
``type: knowledge`` were rejected and the live vault accumulated only a
handful of ``Knowledge/`` notes.

Both legacy constants remain available as aliases re-exported by their
original modules, so every existing caller keeps working unchanged. New code
should import from here.

Stdlib-only — this module is transitively imported by hooks (via
``prompt_templates`` and the select-notes prompt), so it is held to the same
stdlib-only contract as the ``core/`` package and is covered by
``tests/test_stdlib_only.py``.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Note types
# ---------------------------------------------------------------------------

#: Every valid value for the ``type`` frontmatter field. The single source;
#: ``summarizer._state_const._VALID_NOTE_TYPES`` and
#: ``doctor._state.VALID_TYPES`` are aliases over this set.
VALID_NOTE_TYPES: frozenset[str] = frozenset(
    {
        "debugging",
        "research",
        "pattern",
        "tool",
        "framework",
        "language",
        "project",
        "daily",
        "knowledge",
    }
)

#: Map each note type to the vault folder that holds it. Keys are exactly
#: :data:`VALID_NOTE_TYPES`; every type routes somewhere.
TYPE_FOLDERS: dict[str, str] = {
    "debugging": "Debugging",
    "research": "Research",
    "pattern": "Patterns",
    "tool": "Tools",
    "framework": "Frameworks",
    "language": "Languages",
    "project": "Projects",
    "daily": "Daily",
    "knowledge": "Knowledge",
}

#: Fallback folder when a note's type is unrecognized or missing.
DEFAULT_FOLDER: str = "Research"

# ---------------------------------------------------------------------------
# Frontmatter field contract
# ---------------------------------------------------------------------------

#: Fields required on every note, of any type.
REQUIRED_FRONTMATTER_FIELDS: frozenset[str] = frozenset({"date", "type", "tags"})

#: Canonical serialization order for the known frontmatter fields (ARC-005).
#: ``core.vault_index.serialize_frontmatter`` emits these keys first (when
#: present), then any extra fields in insertion order. Mirrors the field order
#: in the note-template frontmatter and ``visualizer/lib/frontmatter.ts``;
#: the parity fixture ``tests/fixtures/parity/frontmatter.json`` pins the
#: Python and TS emitters to this order.
FRONTMATTER_FIELD_ORDER: tuple[str, ...] = (
    "date",
    "type",
    "tags",
    "project",
    "confidence",
    "sources",
    "related",
    "provenance",
    "status",
    "superseded_by",
    "session_id",
)

#: Fields required on knowledge notes (i.e. non-daily). Daily notes are exempt
#: from ``confidence`` and ``related``.
REQUIRED_KNOWLEDGE_FIELDS: tuple[str, ...] = ("confidence", "related")

#: Valid values for the optional ``provenance`` frontmatter field.
VALID_PROVENANCE_VALUES: frozenset[str] = frozenset(
    {"explicit", "inferred", "corrected", "observed", "imported"}
)

#: Valid values for the ``confidence`` frontmatter field.
VALID_CONFIDENCE_VALUES: frozenset[str] = frozenset({"high", "medium", "low"})

# ---------------------------------------------------------------------------
# Supersession (note retirement) contract
# ---------------------------------------------------------------------------

#: Valid values for the optional ``status`` frontmatter field. A note without
#: ``status`` (or with ``status: live``) is a normal, retrievable note;
#: ``status: superseded`` retires it from every retrieval surface while the
#: file, its wikilinks, and its embeddings stay in place for audit.
VALID_STATUS_VALUES: frozenset[str] = frozenset({"live", "superseded"})

#: The retired-note status value every retrieval filter tests for.
STATUS_SUPERSEDED: str = "superseded"


def validate_status_fields(fields: dict[str, object]) -> list[str]:
    """Validate the status/superseded_by frontmatter pair; return error strings.

    The consistency rule behind the ``superseded-consistency`` doctor rule:
    ``status: superseded`` requires at least one resolvable-looking
    ``superseded_by`` entry (the retirement must point at its replacement),
    and ``superseded_by`` on a live note is a contradiction. Wikilink
    *resolution* (the target note existing) is checked by the doctor rule,
    not here — this function sees frontmatter only.

    Returns an empty list when the pair is consistent.
    """
    errors: list[str] = []
    status = fields.get("status")
    if status is not None and not isinstance(status, str):
        errors.append(f"status must be a string, got {type(status).__name__}")
        return errors
    if status is not None and status not in VALID_STATUS_VALUES:
        errors.append(
            f"status must be one of {sorted(VALID_STATUS_VALUES)}, got {status!r}"
        )
        return errors

    superseded_by = fields.get("superseded_by")
    if superseded_by is not None and not isinstance(superseded_by, (list, str)):
        errors.append(
            f"superseded_by must be a list or wikilink string, got "
            f"{type(superseded_by).__name__}"
        )
        return errors

    if status == STATUS_SUPERSEDED:
        entries = _superseded_by_entries(superseded_by)
        if not entries:
            errors.append(
                "status: superseded requires at least one superseded_by wikilink"
            )
    elif superseded_by is not None:
        errors.append("superseded_by set but status is not 'superseded'")
    return errors


def _superseded_by_entries(value: object) -> list[str]:
    """Non-empty wikilink entries from a superseded_by field value."""
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = [v for v in value if isinstance(v, str)]
    else:
        return []
    return [item for item in items if item.strip() and "[[" in item]


# ---------------------------------------------------------------------------
# Tag rules (shared by every prompt that instructs the model on tags)
# ---------------------------------------------------------------------------

#: The kebab-case / short-singular tag rule, stated once. Both branches of the
#: summarizer's tag instruction (existing-tags vs. fresh-vault) interpolate
#: this so a single edit updates both. Prompt templates that mention tags also
#: interpolate ``{tag_rules}`` from this constant.
TAG_RULES: str = (
    "  NEVER use underscores — always kebab-case (hyphens);\n"
    "  prefer short singular tags: 'voxel' not 'voxel-engine', 'hook' not 'hooks')"
)

#: Comma-separated, sorted list of valid note types — the form prompts inject
#: as ``{note_types}``. Pre-computed so prompt rendering does not have to import
#: the frozenset at render time (the loader stays free of domain knowledge).
NOTE_TYPES_DISPLAY: str = ", ".join(sorted(VALID_NOTE_TYPES))
