"""Leaf-level constants shared across vault modules.

ARC-023 cycle 1: ``TRANSCRIPT_CATEGORY_LABELS`` previously lived in
``vault_hooks``, but ``vault_fs.append_session_to_daily`` needs it too.
Because ``vault_hooks`` already imports from ``vault_fs`` at module level
(``flock_exclusive`` / ``funlock``), the reverse import created a
``vault_fs -> vault_hooks -> vault_fs`` cycle that was only broken by a
function-body lazy import.  Hoisting the constant to this true leaf
module (which imports nothing from the other ``vault_*`` modules) lets
both consumers take a top-level import and makes the cycle impossible.

``TRANSCRIPT_CATEGORIES`` (the keyword map consumed by
``vault_hooks.detect_categories``) stays where it is: nothing outside
``vault_hooks`` reads it, so moving it would be churn with no cycle benefit.
"""

from __future__ import annotations

__all__: list[str] = [
    "TRANSCRIPT_CATEGORY_LABELS",
    "HOOK_TIMEOUTS_MS",
]

TRANSCRIPT_CATEGORY_LABELS: dict[str, str] = {
    "error_fix": "Error Resolution",
    "research": "Research Findings",
    "pattern": "Pattern Discovery",
    "config_setup": "Config/Setup",
}

# ENH-019: per-hook registered timeouts in milliseconds, mirroring
# installer/paths.py:_HOOK_OPTIONS so the skill can compute hook-latency
# budget ratios without importing the installer. Hooks absent from this map
# (SessionEnd, SubagentStop) are registered async — no timeout to exceed.
# Keep the two definitions in sync when a hook's registration changes.
HOOK_TIMEOUTS_MS: dict[str, int] = {
    "SessionStart": 60_000,
}
