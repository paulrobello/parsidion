"""AI-output validation and merge-config helpers (ARC-005).

Extracted from ``vault_merge.py``. These helpers support the AI-merge body
pipeline but have no test-patched surface, so they relocate cleanly into
this submodule; ``vault_merge.py`` re-exports them for backwards-compat
with test attribute access (``vault_merge._is_valid_merge_body``,
``vault_merge._configured_merge_model``).

Stdlib-only at module load.
"""

from __future__ import annotations

from pathlib import Path

from core.vault_config import load_config

# Backend-neutral default for the AI-merge timeout (seconds). The original
# ``vault_merge.py`` defined this at module level; it lives here now that
# ``_configured_merge_timeout`` does, and ``vault_merge.py`` re-exports it
# so existing ``vault_merge._DEFAULT_AI_TIMEOUT`` lookups keep resolving.
_DEFAULT_AI_TIMEOUT: int = 60


def _is_valid_merge_body(merged: str) -> bool:
    """Return True if AI output has the shape the merge prompt demands.

    The prompt requires the backend to emit ONLY the merged note body,
    starting with the first markdown heading — no frontmatter, no code
    fences, no prose preamble. Backend refusals and error messages fail
    this shape check, so they can never be written over the keeper note.

    SEC-115: strengthen the guard beyond the previous length≥50 +
    startswith("#") check. A body that begins with YAML frontmatter
    delimiters, is wrapped in a markdown code fence, or opens with a
    common refusal phrase is rejected even when it happens to start
    with ``#`` after a fence line. ``#``-prefixed refusal lines such
    as ``# Error`` are not common in model refusals and are accepted —
    the deeper protection is that the merge body is inline (no
    filesystem access handed to the child) and the assembled note is
    validated before write.
    """
    stripped = merged.strip()
    if len(stripped) < 50:
        return False
    # Must start with a markdown heading — body-only, no frontmatter.
    if not stripped.startswith("#"):
        return False
    # SEC-115: reject common refusal shapes that happen to slip past the
    # ``startswith("#")`` check via a code fence or frontmatter wrapper.
    if stripped.startswith("---"):  # YAML frontmatter mistakenly included
        return False
    if stripped.startswith("```"):  # wrapped in a code fence
        return False
    # Common refusal / can't-do phrases at the very start. Match a handful
    # of canonical forms so a refusal that starts with a heading + apology
    # is caught.
    refusal_prefixes = (
        "# unable to",
        "# i cannot",
        "# i can not",
        "# sorry",
        "# error",
        "# refused",
    )
    head = stripped[:64].lower()
    for pref in refusal_prefixes:
        if head.startswith(pref):
            return False
    return True


def _configured_merge_model(vault_path: Path | None = None) -> str | None:
    """Return an explicitly configured merge model, if any."""
    config = load_config(vault=vault_path)
    summarizer = config.get("summarizer")
    if not isinstance(summarizer, dict) or "merge_model" not in summarizer:
        return None
    model = summarizer["merge_model"]
    if isinstance(model, str) and model.strip():
        return model.strip()
    return None


def _configured_merge_timeout(vault_path: Path | None = None) -> int | float:
    """Return the configured merge timeout or the backend-neutral default."""
    config = load_config(vault=vault_path)
    summarizer = config.get("summarizer")
    if isinstance(summarizer, dict):
        timeout = summarizer.get("merge_timeout")
        if isinstance(timeout, bool):
            return _DEFAULT_AI_TIMEOUT
        if isinstance(timeout, (int, float)):
            return timeout
    return _DEFAULT_AI_TIMEOUT
