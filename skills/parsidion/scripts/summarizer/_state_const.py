"""Sentinels, enums, regexes, and default config constants for the summarizer.

Extracted from ``summarize_sessions.py`` (ARC-009).  Pure constants only — no
``vault_common`` dependency, no side effects at import time.  Both the entry
shim and the leaf submodules import from here so every binder shares one
definition.
"""

from __future__ import annotations

import enum
import re

# ---------------------------------------------------------------------------
# Result sentinels — returned as ``written_path`` by ``summarize_one``.
# ---------------------------------------------------------------------------

# Transcript file no longer exists; entry is purged from the pending queue.
_STALE = "__STALE__"

# Write-gate decided the session is transient; entry is purged so it is not
# reprocessed forever.
_SKIPPED = "__SKIPPED__"

# A session already recorded in dead_letters.jsonl (prior failure or write-gate
# skip) that a stop hook re-queued.  Re-processing would re-bill an AI call for
# a session already judged not worth a note, so it is purged on sight via the
# same path as _STALE.
_DEAD = "__DEAD__"

# A session whose transcript is still being written (an active session).
# Summarizing a mutating transcript is racy, so it is left in the queue for a
# later run once it is genuinely idle.
_DEFERRED = "__DEFERRED__"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# A transcript whose mtime is within this window is treated as an active session
# and deferred.  Long enough to absorb a just-ended session flushing its final
# lines, short enough that a genuinely idle queued session is processed promptly.
_ACTIVE_SESSION_GRACE_SECS = 120

# Dead-letter cap: a queue entry that fails this many times is purged from
# pending_summaries.jsonl instead of retrying (and re-billing an AI call) on
# every run forever.  Tracked via the optional "attempts" field (absent = 0).
_MAX_ATTEMPTS = 3

# Dead-letter retention (days): entries in dead_letters.jsonl older than this
# are pruned on each run.  Write-gate skips are made sticky (a re-queue is
# caught by the _DEAD guard), so without retention the file grows without
# bound.  <= 0 disables pruning.  Configurable via
# summarizer.dead_letter_retention_days.
_DEAD_LETTER_RETENTION_DAYS = 7

# ---------------------------------------------------------------------------
# Failure classification (ARC-030)
# ---------------------------------------------------------------------------

_FAILURE_REASON_KEY = "_failure_reason"


class FailureReason(enum.Enum):
    """Classified failure kind for a summarization attempt.

    ARC-030: replaces the free-text ``reason`` string so ``remove_processed``
    can decide retry-vs-dead-letter from the failure class rather than burning
    ``_MAX_ATTEMPTS`` AI calls on a deterministic failure.  Each member carries
    ``retryable``: when False, the entry is dead-lettered on the FIRST failed
    attempt instead of being re-queued.

    Classifications:
    - Transient (retryable=True): the next run might succeed — network blip,
      transient FS error, model-side hiccup, or unhandled exception that may
      not reproduce.
    - Deterministic (retryable=False): the same model output / config / target
      will fail the same way on every retry.  Re-billing an AI call to re-derive
      the same failure wastes money and (for merge decisions) re-touches a
      trusted note.  Validation/containment failures also indicate either a code
      defect or a crafted transcript — both warrant human attention, not silent
      retries.
    """

    TRANSCRIPT_READ = ("transcript_read", True)
    AI_BACKEND_ERROR = ("ai_backend_error", True)
    NO_RESULT = ("no_result", True)
    BACKUP_FAILED = ("backup_failed", True)
    UNHANDLED = ("unhandled", True)
    MERGE_MALFORMED = ("merge_malformed", False)
    MERGE_UNRESOLVABLE = ("merge_unresolvable", False)
    MERGE_VALIDATION = ("merge_validation", False)
    MERGE_CONTAINMENT = ("merge_containment", False)
    NOTE_VALIDATION = ("note_validation", False)

    def __init__(self, kind: str, retryable: bool) -> None:
        self.kind = kind
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Default config values (overridable via config.yaml)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_PARALLEL = 5
_DEFAULT_TRANSCRIPT_TAIL_LINES = 400
# Byte ceiling on the raw transcript tail, applied in addition to
# transcript_tail_lines.  Bounds transcripts whose few lines are individually
# huge (e.g. codex subagent rollouts) so cleaning/chunking cannot explode.
_DEFAULT_TRANSCRIPT_TAIL_BYTES = 262_144
_DEFAULT_MAX_CLEANED_CHARS = 12_000

# ---------------------------------------------------------------------------
# Note type → folder mapping
# ---------------------------------------------------------------------------

# Map note type values to vault folders
_TYPE_FOLDERS: dict[str, str] = {
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

# Fallback folder when type is unrecognized
_DEFAULT_FOLDER = "Research"

# ---------------------------------------------------------------------------
# Frontmatter validation sets
# ---------------------------------------------------------------------------

_REQUIRED_FRONTMATTER_FIELDS: frozenset[str] = frozenset({"date", "type", "tags"})

# Valid values for the 'type' frontmatter field
_VALID_NOTE_TYPES: frozenset[str] = frozenset(
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

# Valid values for the optional 'provenance' frontmatter field
_VALID_PROVENANCE_VALUES: frozenset[str] = frozenset(
    {"explicit", "inferred", "corrected", "observed", "imported"}
)

# ---------------------------------------------------------------------------
# Regexes used by frontmatter / related-field helpers
# ---------------------------------------------------------------------------

# Matches a YAML frontmatter key line such as 'date:' or 'tags: [...]'.
_FRONTMATTER_KEY_LINE_RE = re.compile(r"^[A-Za-z_][\w.-]*\s*:")

_RELATED_LINE_RE = re.compile(r"^related:\s*(.*)$", re.MULTILINE)
# A stem wrapped in any combination of brackets/quotes: catches [[stem]],
# [stem], [["stem"]], "[[stem]]", etc.  A stem starts with a word char and may
# contain word chars, dots (version slugs), slashes (folder-qualified links),
# and hyphens; it stops at | (alias), # (anchor), or whitespace.
_RELATED_STEM_RE = re.compile(r"[\[\"']+([\w][\w./-]*)[\]\"']+")

# ---------------------------------------------------------------------------
# Cross-process lock filename
# ---------------------------------------------------------------------------

_SUMMARIZER_STATE_FILENAME = "summarizer_state.json"
