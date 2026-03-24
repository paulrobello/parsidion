# Parsidion CC Enhancement Roadmap

This document catalogs all planned enhancements to the parsidion-cc Claude Code
customization toolkit. Enhancements are organized by tier (impact × effort) and
implementation phase.

---

## Implementation Status

| # | Enhancement | Tier | Status |
|---|-------------|------|--------|
| 1 | Hook execution event log | 1 | ✅ Implemented |
| 2 | Git context in PreCompact snapshot | 1 | ✅ Implemented |
| 3 | Pending queue summary at SessionStart | 1 | ✅ Implemented |
| 4 | `vault-stats --pending` mode | 1 | ✅ Implemented |
| 5 | Config validation on startup | 1 | ✅ Implemented |
| 6 | `vault-review` interactive CLI | 2 | ✅ Implemented |
| 7 | Summarizer auto-trigger via hook | 2 | ✅ Implemented |
| 8 | Weekly/monthly rollup notes | 2 | ✅ Implemented |
| 9 | `vault-search` interactive mode | 2 | ✅ Implemented |
| 10 | Cross-session delta at SessionStart | 2 | ✅ Implemented |
| 11 | Knowledge graph analytics | 2 | ✅ Implemented |
| 12 | Auto-subfolder migration in Doctor | 2 | ✅ Implemented |
| 13 | Summarizer progress feedback | 2 | ✅ Implemented |
| 14 | `vault-timeline` CLI | 3 | ✅ Implemented |
| 15 | `vault-export` CLI | 3 | ✅ Implemented |
| 16 | Vault merge tool | 3 | ✅ Implemented |
| 17 | SessionStart adaptive context | 3 | ✅ Implemented |
| 18 | Multi-vault support | 3 | ✅ Implemented |
| 19 | Cron-based auto-summarization | 3 | ✅ Implemented |

---

## Tier 1: High-Impact, Low-Effort (Quick Wins)

### Enhancement #1: Hook Execution Event Log

**Problem:** Hook execution is a black box. When SessionEnd fires and runs
detached, there's no easy way to know if it succeeded, how long it took, or
whether it queued anything.

**Solution:** Append a structured JSON line to `~/ClaudeVault/hook_events.log`
at the end of each hook execution:

```json
{"hook": "SessionEnd", "ts": "...", "project": "parsidion-cc", "queued": true, "categories": {"pattern": 1}, "duration_ms": 1230}
{"hook": "SessionStart", "ts": "...", "project": "...", "notes_injected": 5, "chars": 2800, "duration_ms": 320}
```

**Files modified:** `vault_common.py` (new `write_hook_event()`), `session_stop_hook.py`,
`session_start_hook.py`, `subagent_stop_hook.py`, `vault_stats.py` (new `--hooks` mode)

---

### Enhancement #2: Git Context in PreCompact Snapshot

**Problem:** PreCompact captures current task + recent files, but not git state.
After compaction Claude sometimes forgets what's staged or the current branch.

**Solution:** Add `git status --short` and `git branch --show-current` output to
the Pre-Compact Snapshot section:

```markdown
## Pre-Compact Snapshot (14:35)
- **Project**: parsidion-cc
- **Branch**: feat/vault-stats-improvements
- **Working on**: Fix vault_stats.py tag collection
- **Uncommitted files**: M skills/.../vault_stats.py
- **Recent files**: /path/to/file.py, ...
```

**Files modified:** `pre_compact_hook.py`

---

### Enhancement #3: Pending Queue Summary at SessionStart

**Problem:** Users have no idea how many sessions are queued for summarization.
The queue can grow silently.

**Solution:** Append a warning to injected context when queue is non-empty:
```
⚠ 7 sessions pending summarization (run summarize_sessions.py)
```

**Files modified:** `session_start_hook.py`

---

### Enhancement #4: `vault-stats --pending` Mode

**Problem:** No easy way to see the pending queue status.

**Solution:** New mode showing total entries, breakdown by source, projects
represented, oldest entry timestamp, and estimated token cost.

**Files modified:** `vault_stats.py`

---

### Enhancement #5: Config Validation on Startup

**Problem:** Typos in `config.yaml` silently fall back to defaults.

**Solution:** Add `validate_config()` to `vault_common.py` that checks known
keys against a schema dict. Warns (not crashes) on unknown keys or type
mismatches. Runs during `session_start_hook.py`.

**Files modified:** `vault_common.py`, `session_start_hook.py`

---

## Tier 2: High-Impact, Medium-Effort (Core Improvements)

### Enhancement #6: `vault-review` Interactive CLI

**Problem:** Users can't inspect/approve/reject sessions before AI spends tokens.

**Solution:** New `vault-review` TUI showing pending sessions with `y/n/s`
approval controls. `summarize_sessions.py` respects `--approved-only` flag.

**New file:** `vault_review.py`
**Entry point:** `vault-review`

---

### Enhancement #7: Summarizer Auto-Trigger via Hook

**Problem:** Summarization requires manual invocation from a separate terminal.

**Solution:** Add `auto_summarize_after: N` config. After SessionEnd, if queue
size ≥ threshold, launch summarizer detached. Defaults to always-trigger when
threshold is 1 (compatible with existing `auto_summarize: true` behavior).

**Files modified:** `session_stop_hook.py`, `config.yaml`

---

### Enhancement #8: Weekly/Monthly Rollup Notes

**Problem:** No higher-level synthesis across days/weeks.

**Solution:** `vault-stats --weekly` and `--monthly` generate rollup notes
summarizing all daily notes for the period.

**Files modified:** `vault_stats.py`

---

### Enhancement #9: `vault-search` Interactive Mode

**Problem:** vault-search is a one-shot CLI; no iterative query refinement.

**Solution:** `vault-search --interactive` / `-i` launches a `curses`-based TUI
with real-time results, navigation, and editor integration.

**Files modified:** `vault_search.py`

---

### Enhancement #10: Cross-Session Delta at SessionStart

**Problem:** SessionStart injects generic notes, not what changed since the
last session in this project.

**Solution:** Track `last_seen` per project in a JSON sidecar. At SessionStart,
prepend a "Since last time" section with newly created/modified notes.

**Files modified:** `session_start_hook.py`, `vault_common.py`

---

### Enhancement #11: Knowledge Graph Analytics

**Problem:** No relationship/graph metrics in vault-stats.

**Solution:** `vault-stats --graph` shows average degree, hub notes, isolated
clusters, orphans, and citation chains.

**Files modified:** `vault_stats.py`

---

### Enhancement #12: Auto-Subfolder Migration in Vault Doctor

**Problem:** The 3+ prefix convention is currently a manual process.

**Solution:** `vault_doctor.py --migrate-subfolders` detects prefix groups,
shows candidates, and with `--execute` moves files and updates wikilinks.

**Files modified:** `vault_doctor.py`

---

### Enhancement #13: Summarizer Progress Feedback

**Problem:** `summarize_sessions.py` runs silently for large queues.

**Solution:** Write progress JSON to `/tmp/parsidion-cc-summarizer-progress.json`.
`vault-stats --summarizer-progress` reads this file for live feedback.

**Files modified:** `summarize_sessions.py`, `vault_stats.py`

---

## Tier 3: Nice-to-Have

### Enhancement #14: `vault-timeline` CLI

Chronological activity view: bar chart of notes per day (last 90 days),
filterable by project/tag, with links to daily notes.

**New file:** `vault_timeline.py` (implemented within `vault_stats.py --timeline`)

---

### Enhancement #15: `vault-export` CLI

Export vault to HTML static site, filtered zip, or PDF via pandoc.

**New file:** `vault_export.py`
**Entry point:** `vault-export`

---

### Enhancement #16: Vault Merge Tool

AI-assisted merging of near-duplicate notes with backlink updates.

**New file:** `vault_merge.py`
**Entry point:** `vault-merge`

---

### Enhancement #17: SessionStart Adaptive Context

Track which injected notes were referenced by Claude and derank unused ones.

**Files modified:** `session_start_hook.py`, `vault_common.py`

---

### Enhancement #18: Multi-Vault Support

`--vault PATH` flag on all CLIs for cross-vault operations.

**Files modified:** `vault_common.py`, `vault_search.py`, `vault_stats.py`,
`vault_new.py`, `vault_doctor.py`

---

### Enhancement #19: Cron-Based Auto-Summarization

Installer `--schedule-summarizer` flag generates a launchd plist (macOS) or
cron job (Linux) for nightly summarization.

**Files modified:** `install.py`

---

## Architecture Notes

### Config Schema (additions in `config.yaml`)

```yaml
session_start_hook:
  # Existing keys ...
  track_delta: true          # #10 — track per-project last-seen timestamp

session_stop_hook:
  # Existing keys ...
  auto_summarize_after: 1    # #7 — queue threshold before auto-launching summarizer

event_log:
  enabled: true              # #1 — write hook_events.log
  max_lines: 10000           # #1 — rotate after this many lines

adaptive_context:
  enabled: false             # #17 — track note usefulness
  decay_days: 30             # #17 — half-life for deranking unused notes
```

### New CLI Entry Points (`pyproject.toml`)

```
vault-review    → vault_review:main
vault-export    → vault_export:main
vault-merge     → vault_merge:main
```

### New Files

| File | Purpose |
|------|---------|
| `vault_review.py` | Interactive pending-queue review TUI (#6) |
| `vault_export.py` | Vault export to HTML/PDF/zip (#15) |
| `vault_merge.py` | AI-assisted note merging (#16) |

### Hook Event Log Format

Each line in `~/ClaudeVault/hook_events.log` is a JSON object:

```typescript
{
  hook: "SessionStart" | "SessionEnd" | "SubagentStop" | "PreCompact",
  ts: string,           // ISO 8601
  project: string,
  duration_ms: number,
  // Hook-specific fields:
  notes_injected?: number,   // SessionStart
  chars?: number,             // SessionStart
  queued?: boolean,           // SessionEnd
  categories?: object,        // SessionEnd
  agent_type?: string,        // SubagentStop
}
```
