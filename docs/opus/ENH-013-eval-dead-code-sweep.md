# ENH-013 — Wire-or-delete dead helpers in `tools/eval/`

## Goal
Reduce `find_dead_code` noise and make the eval harness's live surface honest by wiring-or-deleting the genuinely-dead helpers the code-quality audit (QA-009) identified — distinguishing them from the false positives.

## Current-state context
- par-mem `find_dead_code` flags ~37 functions, but the audit classified most as false positives:
  - **Not dead** (leave alone): MCP tools registered via `mcp.tool()` decorator (`ops.py:rebuild_index`, `vault_health`; `search.py:vault_search` — wired in `server.py`); React components consumed by JSX (`GraphCanvas`, `ViewToggle`); the pi extension default export (`extensions/pi/parsidion.ts:525`); Next.js config methods (`next.config.ts:headers`); SSE controller `start`/`cancel` (`graph/route.ts`, `vault/events/route.ts`); `StepList.run_all` (called via lambda).
  - **Genuinely dead** (this enhancement): cluster in `tools/eval/` — `embed_eval_report.py:display_results` (L39), `embed_eval_report.py:save_json_results` (L97), per-evaluator `_load_inputs` (`detect_conflicts.py:31`, `merge_notes.py:30`, `repair_frontmatter.py:45`, `select_notes.py:40`, `summarize_chunk.py:41`, `summarize_session.py:41`), `evaluators/_base.py:BaseEvaluator.version_stamp` (L212).
- `tools/eval/` is a developer-only embedding/prompt eval harness; a board card already notes `prompt_eval_run.py` only wires golden cases for one prompt (ENH-008 follow-up). So some "dead" `_load_inputs` may be *not-yet-wired* rather than truly dead.

## Step-by-step implementation
1. **Triage each candidate**: for each genuinely-dead symbol, determine wire-vs-delete:
   - `_load_inputs` on each evaluator: check whether the eval driver dispatches through a base-class method that *should* call `_load_inputs`. If the harness was refactored to bypass it, delete; if it is part of the not-yet-wired per-prompt path (the ENH-008 follow-up), either wire it now or leave a `# used by prompt_eval_run once ENH-008 lands` comment + skip.
   - `embed_eval_report.display_results` / `save_json_results`: if the CLI now uses `generate_html_report` exclusively, delete the unused functions; otherwise wire them behind a flag.
   - `BaseEvaluator.version_stamp`: if no report consumer reads the stamp, delete; else wire into the report output.
2. **Verify each decision** with `get_symbol_context` / `analyze_relationships` (repository_id `parsidion`) before deleting — confirm zero non-test callers.
3. **Delete or wire**, one evaluator at a time, running the eval CLI after each.
4. **Re-run `find_dead_code`** and confirm the `tools/eval/` cluster is gone (the false positives elsewhere remain by design).

## Files to touch
- `tools/eval/embed_eval_report.py` (`display_results`, `save_json_results`)
- `tools/eval/evaluators/_base.py` (`version_stamp`)
- `tools/eval/evaluators/{detect_conflicts,merge_notes,repair_frontmatter,select_notes,summarize_chunk,summarize_session}.py` (`_load_inputs`)
- Possibly `tools/eval/embed_eval_run.py` (if wiring `_load_inputs` into the dispatch)

## Verification
- `cd tools/eval && python embed_eval_run.py --help` (and a real run on a small fixture) still works after each change.
- `make checkall` (the eval tools are not in the main gate unless covered by `tests/`; confirm none break).
- `find_dead_code` (repository_id `parsidion`) re-run shows the `tools/eval/` candidates resolved.

## Rollback
- Each deletion is a single-function removal in a dev-only harness; revert per-file from git. No runtime/production impact — the eval harness is developer tooling.
