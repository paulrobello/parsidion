# ENH-007 — `vault-stats --health`: one composite vault health score

> **Impact**: medium · **Effort**: small · **Status**: not started
> Source: Opus deep audit, 2026-07-28, commit `8e5d549`

## Goal

Collapse seven scattered diagnostic flags into one scored report that tells a user what state their
vault is in and what to do about it. Make it the default output of bare `vault-stats`.

## Current state

`vault_stats.py` already computes nearly everything needed, spread across `--summary`, `--stale`,
`--top-linked`, `--by-project`, `--growth`, `--tags`, `--pending`, `--graph`, `--hooks`, and
`--dashboard`. The data is there; the synthesis is not. A user has to know which flags to run, run
several, and interpret the combination themselves.

Meanwhile the audit surfaced real, checkable vault problems that no single flag names:

- `graph.json` was **16 days stale** (`meta.generated: 2026-07-13` at audit time on 2026-07-28).
- **6 sessions dead-lettered** after repeated failures, plus 2 pending — visible only via
  `--pending` and only if you look.
- Only **3 `knowledge` notes** in a 5,563-note vault, because the summarizer structurally cannot emit
  that type (audit item ARC-010). A type-distribution check would have flagged this months ago.
- Vault files at unexpected permissions (audit items SEC-109, SEC-110, SEC-112, SEC-114).
- Tracked files that should be gitignored (audit item SEC-104).

The pattern is that these are all *observable* and all currently *unobserved*. A health command is the
cheapest way to make the vault self-reporting.

Note this is deliberately a **reporting** feature. It must not mutate the vault. `vault-doctor` is the
tool that fixes things; this one tells you to run it.

## Design

Seven scored dimensions, each 0–100, with a weighted overall grade and — most importantly — a concrete
next action per dimension. A score with no action is just a number.

| Dimension | Weight | Measures | Data source |
|---|:---:|---|---|
| **Index freshness** | 20 | newest `note_index.mtime` vs newest `.md` on disk; `graph.json` `meta.generated` age | `embeddings.db`, `graph.json` |
| **Queue health** | 20 | pending count and age; dead-letter count | `pending_summaries.jsonl`, `dead_letters.jsonl` |
| **Graph connectivity** | 15 | orphan notes (no `related`, no backlinks); isolated clusters; mean degree | `note_index`, `graph.json` |
| **Metadata quality** | 15 | frontmatter validity; broken wikilinks; missing required fields; type distribution | `vault_doctor` scan (read-only) |
| **Embedding coverage** | 10 | notes with embeddings ÷ total notes | `embeddings.db` |
| **Tag hygiene** | 10 | near-duplicate tags; singleton tags; underscore violations | `note_index.tags` |
| **File hygiene** | 10 | vault-resident files at unexpected modes; gitignored-but-tracked files | `os.stat`, `git ls-files` |

Grades: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F below. Weights live in one module constant so they are
adjustable without touching scoring logic.

Target output:

```
Vault Health — ~/ParsidionVault                                    B  (82/100)

  Index freshness      ██████░░░░  62   graph.json is 16 days old
                                        → uv run install.py --schedule-summarizer --rebuild-graph
  Queue health         ████░░░░░░  45   2 pending, 6 dead-lettered
                                        → vault-review     # inspect, then re-queue or clear
  Graph connectivity   █████████░  91   14 orphan notes
                                        → vault-doctor --fix-all
  Metadata quality     ██████████  98   all frontmatter valid
  Embedding coverage   █████████░  94   331 of 5563 notes unembedded
                                        → update_index.py   (rebuilds embeddings in background)
  Tag hygiene          ███████░░░  76   23 singleton tags, 4 near-duplicate pairs
                                        → vault-doctor --fix-tags
  File hygiene         █████░░░░░  55   4 tracked files match .gitignore patterns
                                        → git -C ~/ParsidionVault rm --cached <files>

  Note types: pattern 3309 · debugging 1338 · research 494 · project 210 · tool 136
              daily 31 · framework 24 · language 7 · knowledge 3
  ⚠  'knowledge' is underrepresented (3 notes) — the summarizer may not be routing to it
```

## Implementation

### Step 1 — Scoring module

New `skills/parsidion/scripts/vault_health.py`, **stdlib-only** (it is imported by `vault_stats.py`,
which is a PEP 723 script, but keeping health scoring stdlib-only means the MCP server and hooks can
use it too).

```python
@dataclass(frozen=True)
class DimensionScore:
    name: str
    score: int              # 0-100
    weight: int
    detail: str             # human-readable finding
    action: str | None      # exact command to run, or None when healthy

@dataclass(frozen=True)
class HealthReport:
    vault: Path
    dimensions: list[DimensionScore]
    overall: int
    grade: str
    note_types: dict[str, int]
    warnings: list[str]
```

One `score_*(vault) -> DimensionScore` function per dimension. Each must be independently testable and
must **degrade rather than raise** — a missing `graph.json` scores the freshness dimension low with the
detail "graph.json not found", it does not crash the command. Reuse existing helpers from
`vault_metrics.py` and `vault_common` rather than reimplementing counts.

### Step 2 — Scoring curves

Do not invent thresholds silently; put them in one documented constants block so they are arguable:

- **Index freshness**: 100 at ≤ 1 day, linear to 0 at ≥ 30 days; `graph.json` and `note_index` scored
  separately and averaged.
- **Queue health**: `100 - (pending × 2) - (dead_letters × 5)`, floored at 0. Dead letters weigh more
  because they represent *permanently* lost summaries, not merely delayed ones.
- **Graph connectivity**: `100 × (1 - orphans / total)`, with a penalty for isolated clusters.
- **Metadata quality**: `100 × (1 - invalid_notes / total)`.
- **Embedding coverage**: straight percentage.
- **Tag hygiene**: penalize singleton-tag ratio and near-duplicate pairs.
- **File hygiene**: fixed deductions per class of finding.

### Step 3 — Reuse `vault_doctor`'s scan, do not duplicate it

The metadata dimension needs frontmatter validity and broken-wikilink counts, which `vault_doctor.py`
already computes. Call its **scan** path in read-only mode (`--scan-only` semantics) and consume the
result. Do **not** reimplement validation — a second implementation would drift, and this repo already
has that problem three times over (two vault resolvers, three `findNote`s, two `findParsidionScript`s).

If `vault_doctor`'s scan is not currently importable as a function, extract it as one. That extraction
is small and is also useful to audit item ARC-008 (Phase 5), which decomposes that file behind a
`Fixer` protocol with a `detect()` method — align with that shape if ARC-008 has landed.

Note the broken-wikilink scanner has a known false-positive class (code fences, YAML frontmatter inline
comments — see the vault note on `parsidion-doctor-broken-wikilink-codeblock-false-positive`). Use
whatever the current fixed scanner reports; do not add a second filter here.

### Step 4 — CLI wiring

- Add `--health` to `vault_stats.py`.
- Make **bare `vault-stats`** print the health report. Preserve every existing flag exactly — this is
  additive, and changing the no-arg default is the only behaviour change.
- Add `--json` for machine consumption, so the MCP server and the visualizer can render it.
- Respect the existing `VAULT_STATS_FORMAT` / no-colour conventions already used in this file.

Note audit item QA-014 flags `vault_stats.py:981 main` at cyclomatic complexity 37 (argparse dispatch).
Adding another flag makes that marginally worse — if QA-014's `{flag: handler}` table has landed, add
the new mode as a table entry rather than another branch.

### Step 5 — Surface it elsewhere

- `parsidion-mcp`: a `vault_health` tool returning the JSON form. Per audit item ARC-021, give it an
  optional `vault` parameter from the start rather than hardcoding the default vault.
- Visualizer: a health card on the stats panel, consuming `--json` via the existing
  `lib/vaultStatsServer.ts` path.
- Optionally, a one-line health summary in the session-start hook when the grade is D or F — but gate
  it behind a config key (`session_start_hook.show_health`, default `false`). The session-start context
  budget is already tight and this must not silently consume it.

### Step 6 — Tests

`vault_stats.py` sits at **12%** coverage and audit item QA-007 found its existing tests assert nothing.
Do not extend that pattern.

1. Per-dimension: a synthetic vault engineered to score exactly a known value.
2. Missing `graph.json` / missing `embeddings.db` / empty vault → degrades, does not raise.
3. Overall score equals the weight-weighted mean of dimension scores (assert arithmetically).
4. Grade boundaries: 90 → A, 89 → B, and so on.
5. `--json` output validates against a committed schema.
6. **Assert on rendered output with `capsys`** — the actual counts and action strings, not merely that
   the command did not raise. This is the specific weakness QA-007 identified.
7. A perfectly healthy vault scores 100/A and emits no `action` strings.

## Files to touch

| File | Change |
|---|---|
| `skills/parsidion/scripts/vault_health.py` | new — scoring module |
| `skills/parsidion/scripts/vault_stats.py` | `--health`, `--json`, bare-invocation default |
| `skills/parsidion/scripts/vault_doctor.py` | expose the scan as an importable read-only function |
| `parsidion-mcp/src/parsidion_mcp/tools/ops.py` | `vault_health` tool with a `vault` parameter |
| `visualizer/components/VaultStats.tsx` | health card |
| `tests/test_vault_health.py` | new — the seven tests above |
| `CLAUDE.md`, `README.md`, `docs/ARCHITECTURE.md` | document the command and the dimensions |

## Verification

```bash
uv run ruff format --check . && uv run ruff check . && uv run pyright .
uv run pytest tests/test_vault_health.py -v
make checkall

# Against the real vault — it should reproduce findings the audit made by hand
uv run skills/parsidion/scripts/vault_stats.py --health
# Expect it to independently surface: stale graph.json, the dead-letter backlog,
# and the knowledge-type underrepresentation. If it does not, the dimension is wrong.

uv run skills/parsidion/scripts/vault_stats.py --health --json | python3 -m json.tool

# Coverage must actually improve, given the 12% starting point
uv run pytest tests/ --cov=skills/parsidion/scripts --cov-report=term-missing | grep vault_health
```

The real-vault check is the acceptance criterion: this feature is only useful if it finds, unprompted,
the problems a deep audit found by hand.

## Rollback

Purely additive apart from the bare-invocation default. Revert that one behaviour by restoring the
previous no-arg path; everything else is a new module and a new flag. No vault mutation anywhere, so
there is nothing to undo on the data side.

## Risks

- **Scores invite gaming rather than fixing.** Mitigated by pairing every deduction with a concrete
  command. A dimension that cannot name an action should not be scored.
- **Weight bikeshedding.** Put the weights in one constant with a comment explaining the rationale, and
  treat adjustments as cheap. They are.
- **Scan cost on a large vault.** The metadata dimension is the expensive one. Cache it against
  `note_index` mtime, and add `--fast` to skip it if the full run exceeds a couple of seconds on 5,563
  notes.
- **Adding to `vault_stats.py`'s complexity.** Real but small — coordinate with QA-014 as noted in Step 4.
