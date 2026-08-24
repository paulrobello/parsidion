# ENH-015 — Per-rule selection for `vault_doctor` (`--only` / `--skip`) and a rule report

> Status: not started (filed 2026-08-23, Fable audit cycle)
> Impact: high · Effort: M · Depends on: QA-005 (doctor rule registry) from `AUDIT-REMEDIATION-PLAN.md`

## Goal

Let a doctor run enable or exclude individual rules by name, and print a per-rule summary, so a
bulk `--fix-all` can leave out the historically risky rules (prefix-cluster splits, tag merges,
daily-note migration, AI wikilink repair) without giving up the safe ones.

## Current state

- `skills/parsidion/scripts/doctor/check.py:182` `check_note` runs eight checks inline; there is no
  name for an individual check and no way to disable one.
- `doctor/cli.py:317-324` `--fix-all` turns on every fix flag at once, including `--strip-prefixes`
  (vault-wide bulk rename).
- The memory notes record four separate regressions caused by one rule firing when the user only
  wanted another (`parsidion_doctor_prefix_cluster_naive_splits`, `parsidion_doctor_tag_merge_plural_dominance`,
  `parsidion_doctor_broken_wikilink_codeblock_false_positive`, `parsidion_doctor_ai_wikilink_repair_daily_note_substitution`).
- QA-005 introduces `Rule(name, check, fix)` objects registered in a `RULES` list in
  `doctor/protocol.py`. This enhancement builds on that list; do not start it before QA-005 lands.

## Implementation

1. **Name the rules.** In `doctor/protocol.py`, give every `Rule` a stable kebab-case `name`
   (`frontmatter-syntax`, `required-fields`, `related-links`, `broken-wikilinks`, `tags`,
   `headings`, `subfolder-prefix`, `daily-namespace`, `permissions`, `strip-prefixes`). Add
   `risk: Literal["safe", "bulk"]` so bulk renames are self-describing.
2. **CLI flags.** In `doctor/cli.py` `_build_parser` (`:76-247`), add repeatable `--only RULE` and
   `--skip RULE` (`action="append"`, `choices=[r.name for r in RULES]`), plus `--list-rules` that
   prints name, risk, and one-line description and exits 0. `--only` and `--skip` are mutually
   exclusive (`add_mutually_exclusive_group`).
3. **Selection.** Add `select_rules(only, skip) -> list[Rule]` in `protocol.py`; thread the
   selected list through `DoctorOptions` (QA-005) into `run_scan_and_repair` and
   `check_note`. `--fix-all` keeps its current meaning but honours `--skip`.
4. **Per-rule report.** In `doctor/orchestrator.py`, count issues found and fixed per rule name and
   print a table at the end of the run (`rule | found | fixed | skipped`). Include it in the
   `--json` output if the doctor has one; otherwise stdout only.
5. **Docs.** Update `skills/parsidion/SKILL.md` and `CLAUDE.md` doctor sections with the new
   flags and a recommended safe bulk invocation:
   `vault_doctor.py --fix-all --skip strip-prefixes --skip subfolder-prefix`.

## Files to touch

- `skills/parsidion/scripts/doctor/protocol.py`
- `skills/parsidion/scripts/doctor/cli.py`
- `skills/parsidion/scripts/doctor/orchestrator.py`
- `skills/parsidion/scripts/doctor/check.py`
- `tests/test_vault_doctor_orchestrator.py`, new `tests/test_doctor_rule_selection.py`
- `skills/parsidion/SKILL.md`, `CLAUDE.md`

## Verify

- `uv run --no-project skills/parsidion/scripts/vault_doctor.py --list-rules` prints every rule with
  a risk column and exits 0.
- `uv run pytest tests/test_doctor_rule_selection.py -q` passes with cases: `--only tags` runs only
  the tags rule; `--skip strip-prefixes --fix-all` performs no renames on a fixture vault that has
  a prefix cluster; `--only x --skip y` exits 2.
- The per-rule table appears in `--dry-run` output for a fixture vault.
- `make checkall` exit 0.

## Rollback

Pure additive flags; revert the commit. No data migration.
