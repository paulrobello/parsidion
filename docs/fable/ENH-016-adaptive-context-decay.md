# ENH-016 — Implement `adaptive_context.decay_days` (time decay of usefulness scores)

> Status: done (2026-08-24; kanban 01a0319f518172b0835aec3506deb199)
> Impact: medium · Effort: S · Related: DOC-004 (marks the key "reserved" until this lands)

## Goal

Make the documented `adaptive_context.decay_days` key do what CLAUDE.md and the config template
already promise: notes that keep being injected but never used are deranked more strongly the
longer they go unused, and a note's usefulness recovers when it is used again.

## Current state

- `skills/parsidion/scripts/core/vault_adaptive.py` stores per-stem usefulness scores
  (`load_usefulness_scores` `:141`, `update_usefulness_scores` `:200`) and last-seen timestamps
  (`load_last_seen` `:86`, `save_last_seen` `:103`), but no function reads `decay_days`.
- `core/vault_schema.py:279` declares `decay_days: int | float` under `AdaptiveContextConfig`;
  `session_start_hook.py:495` reads only `adaptive_context.enabled`.
- The embeddings section has a separate, implemented recency decay (`decay_enabled`,
  `decay_half_life_days`, `decay_min_factor` at `vault_schema.py:211-213`) used by
  `vault_search.py`; do not confuse the two.

## Implementation

1. **Score model.** In `vault_adaptive.py`, add
   `effective_score(record: dict, now: float, decay_days: float) -> float`: the stored
   `score` multiplied by `0.5 ** (days_since_last_used / decay_days)` where `last_used` is the
   record's last positive-use timestamp (add the field if absent; treat missing as `last_seen`).
   `decay_days <= 0` disables decay (returns the raw score).
2. **Ranking.** Where `session_start_hook.py` (or `session_start/` submodules) sorts candidates by
   usefulness when `adaptive_enabled`, call `effective_score` with
   `get_config("adaptive_context", "decay_days", 30)` instead of reading `score` directly.
   Locate the sort with par-mem `get_symbol_context` on `load_usefulness_scores`.
3. **Recovery.** In `update_usefulness_scores`, when a note is marked used, set
   `last_used = now` so the decay clock resets.
4. **Config.** Set the schema default to `30`, keep the template value, and change the CLAUDE.md
   config row and template comment from "reserved" (DOC-004 wording) to the real semantics.
5. **Tests.** New `tests/test_vault_adaptive_decay.py`: a note unused for `2 * decay_days`
   ranks below a fresh note with half its raw score; `decay_days: 0` reproduces the pre-change
   order; marking used resets the decay.

## Files to touch

- `skills/parsidion/scripts/core/vault_adaptive.py`
- `skills/parsidion/scripts/session_start_hook.py` (or the `session_start/` module holding the sort)
- `skills/parsidion/scripts/core/vault_schema.py`
- `skills/parsidion/templates/config.yaml`, `CLAUDE.md`
- `tests/test_vault_adaptive_decay.py`

## Verify

- `uv run pytest tests/test_vault_adaptive_decay.py tests/ -q -k adaptive` passes.
- `python skills/parsidion/scripts/session_start_hook.py <<< '{"cwd":"/Users/probello/Repos/parsidion"}'`
  with `adaptive_context.enabled: true` and `decay_days: 1` in a temp vault demotes a note whose
  `last_used` is 10 days old below one used today (assert via the injected order).
- `grep -n "reserved" CLAUDE.md skills/parsidion/templates/config.yaml` no longer mentions
  `decay_days`.
- `make checkall` exit 0.

## Rollback

Set `decay_days: 0` in config to disable without reverting; or revert the commit. The
usefulness JSON gains one optional field (`last_used`); older readers ignore it.
