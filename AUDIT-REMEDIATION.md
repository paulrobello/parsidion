# Audit Remediation Report

> Companion to `AUDIT.md` (2026-08-26 Fable 5 deep audit) and `AUDIT-REMEDIATION-PLAN.md`.
> Branch: `fix/audit-remediation` (52 commits over `main`, tip `f5cfd8b`).
> This session resumed a prior session that was interrupted mid-run by a 5-hour usage-limit
> (429) error partway through Wave 2 / cluster A; nothing was lost — both worktrees and
> branches survived and were verified before continuing.

## Outcome

25 issues in scope. **23 resolved**, **2 skipped** (blocked on open backlog decision cards,
as the plan specified). Full gate green on the merged branch.

| Phase | Issues | Result |
|---|---|---|
| Phase 2 — ARC-101 | 1 | ✅ resolved |
| 3a — Security (SEC-201..206) | 6 | ✅ all resolved |
| 3b-1 — Architecture cluster A (PRF-101, ARC-102, PRF-102, PRF-103, ARC-103) | 5 | ✅ all resolved |
| 3b-2 — Architecture cluster B (ARC-104, PRF-104, ARC-106..110, PRF-105) | 8 | ✅ all resolved |
| 3c — Code Quality | 8 | ✅ 6 resolved (QA-101,105,107,108,109,111); ⏭️ 2 skipped (QA-104, QA-110) |
| 3d — Documentation (DOC-101..109) | 9 | ✅ all resolved |

### Skipped (by design, not oversight)

- **ARC-105** (AgentAdapter/InstallerSpec split) — precondition backlog card "Remove or wire
  dead AgentAdapter fields `runtime_env_value` and `instructions_filename`" is still open.
  Still on the backlog; unblock that card first.
- **QA-104** (`render_vaults_yaml` structured-model rewrite, merged ARC-111) — precondition
  backlog card "Decide fate of vaults.yaml top-level `default:` key" is still open. Still on
  the backlog.
- **QA-110** (CLI main complexity cluster) was explicitly opportunistic in the plan ("take
  each file ONLY if it is already being touched by another entry this cycle"). None of its
  six target files (`vault_merge.py`, `installer/cli.py`, `installer/skill.py`,
  `html-to-md.py`, `pre_compact_hook.py`, `doctor/daily.py`) ended up edited by another
  entry, so it was correctly left untouched. Still on the backlog if wanted later.

## Verification performed

- `make checkall` — green on the final merged tree, run from the main checkout on
  `fix/audit-remediation`: fmt-check, lint, pyright (0 errors), full pytest suite (1,823+
  tests passed), test-graph, visualizer-check (tsc + lint + tests + prod build),
  checkall-mcp (76 passed), config-docs-check.
- `make docs-api` + `make docs-api-check` — run from the **main checkout** (not a worktree,
  per the typedoc git-remote constraint) after ARC-104's perl→Python port. Diffed the
  regenerated output against the pre-remediation tree; confirmed every changed line traces
  to a real docstring/schema change from this cycle (e.g. `ai_candidates_max` becoming
  `int | None`, the new `grok_cli.allow_tools` field) and found zero leaked local paths or
  frozenset-ordering nondeterminism — the byte-exactness bar ARC-104 set for its own port.
  Committed as `f5cfd8b`.
- Functional smoke test: `session_start_hook.py` invoked from `/tmp` (a different cwd than
  the target project) still resolves the correct vault and returns valid context — confirms
  ARC-101's vault-threading fix works end-to-end, not just under unit tests.
- Each sub-agent additionally ran its own targeted `Verify` commands per
  `AUDIT-REMEDIATION-PLAN.md` entry before committing (visible in individual commit history).

## Notable findings during execution (beyond the original plan)

- **Cross-worktree seam** (Wave 1): SEC-205's new tests used the `load_config.cache_clear()`
  idiom that QA-101's cache rework had just removed, invisible to both agents since they
  worked in parallel worktrees. Fixed at the merge seam (`ed083b8`).
- **ARC-103's own step 4 was a no-op**: the plan's "internal consumers" comment block it
  says to trim doesn't exist anywhere in the repo, and no shim ever reached zero internal
  consumers to retire from it — `doctor/` (14 files) still imports the flat shims and was
  never in ARC-103's file list. Proposed as a follow-up "group 5" (see Follow-ups below).
- **No `pyproject.toml` F401 changes were warranted**: the star-import shims' exemptions are
  dead code from `import *` usage, not internal-use exemptions — dropping them was correctly
  identified as unrelated scope.
- **ARC-104's differential port testing caught a real bug** before it shipped: `&amp;#39;`
  ends with `;`, not `'` — an early port attempt mishandled this and was caught by testing
  against all 265 committed `docs/api` HTML/JS files as an oracle, not just synthetic
  fixtures.
- **PRF-104's review pass caught two silently-lost invariants** in code it touched but
  wasn't asked to verify: `git commit --only -- <unchanged>` really does print a recognized
  no-op signature (confirmed empirically, not assumed), and `note_index.mtime` really is the
  file's mtime — both load-bearing for the new snapshot-based delta not silently regressing
  into a full-vault listing.
- **ARC-103 group 2 (summarizer/) silently broke test coverage**, caught by an agent probing
  seams it hadn't rewritten: three `test_summarize_sessions.py` tests patched `get_config` on
  the shim, which used to also cover `summarizer.queue._bool_option`'s resolution — after the
  import conversion they kept passing while no longer pinning anything. Restored in `d4904cd`.

## Follow-ups filed to the backlog (not part of this cycle's scope)

- **ARC-103 group 5 (`doctor/`)**: proposed by the cluster-A agent; the 14-file `doctor/`
  subpackage still imports via the flat `vault_common`/`vault_fs`/`vault_links`/`ai_backend`
  shims and was never named in ARC-103's file list. Left as-is; a future ARC-103 follow-on
  card should target it.
- Enhancement plans `ENH-020` through `ENH-024` were written during the original audit cycle
  (already committed at `6c983d4`, ahead of this report) and remain on the backlog,
  unaffected by this remediation.

## Board reconciliation

Closed on the `parsidion` kanban project (the only four cards this remediation cycle had —
every other issue had no dedicated card, per the plan):

- `[ARC-101]` Thread resolved vault through config/DB lookups → done
- `[QA-101]` Fix load_config shared-dict cache → done
- `[DOC-101]` Rewrite ARCHITECTURE.md doctor-locking section → done
- `[PRF-101]` In-process parsight path for SessionStart semantic search → done

Left untouched (still open, correctly so): the two blocking backlog decision cards
(vaults.yaml `default:` key fate; dead AgentAdapter fields) that ARC-105/QA-104 depend on.

## What's left before this branch can ship

1. **Merge `fix/audit-remediation` into `main`** (52 commits, currently unmerged; not done
   automatically — this is an outward-facing/irreversible action).
2. Optional: a `CHANGELOG.md` entry summarizing this remediation cycle (not added — 23
   internal fixes across security/perf/quality/docs is a judgment call on changelog
   granularity, left to the user).
3. Optional: delete `AUDIT.md` and `AUDIT-REMEDIATION-PLAN.md` — both are transient audit
   artifacts per this cycle's own DOC-107 fix ("AUDIT.md is transient and deleted by
   `/fix-audit`"). This report (`AUDIT-REMEDIATION.md`) is the durable record.
