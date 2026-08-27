# ENH-023 — SessionStart latency benchmark harness and budget gate

## Goal

Make SessionStart hook latency a measured, regression-gated quantity: a repeatable local benchmark (`make bench-hooks`) that runs the hook against a synthetic vault at several sizes, reports wall-time breakdown per stage, and fails when the total exceeds a budget — so changes like PRF-101/PRF-104 (audit 2026-08-26) are provably kept and future regressions are caught before users feel them.

## Current state

- ENH-019 added *observational* latency: `hook_events.log` durations, `vault-stats --hooks N --hooks-window D` percentiles, and a p95-vs-70%-of-60s SessionStart warning (`cli/stats/operations.py:204`). Nothing measures latency *pre-merge* or attributes it to stages.
- The audit quantified the current costs only by estimation (0.5–8 s subprocess tower, ~50–150 ms full-table loads at 5k notes); there is no fixture to reproduce those numbers.
- Hooks are testable via stdin JSON (documented in CLAUDE.md), so a harness needs no Claude Code runtime.

## Implementation

1. **Synthetic vault generator.** `tools/bench/gen_bench_vault.py` (stdlib-only not required — tools/ is outside the gate, but keep it stdlib for simplicity): create a temp vault with N notes (parameter; default runs N=500 and N=5000) with realistic frontmatter, tags, `related` links (power-law-ish out-degree), a populated `note_index`/`embeddings.db` via the real `update_index.py` (skip actual embeddings — metadata only, or fake vectors — semantic leg is stubbed, see step 3).
2. **Bench driver.** `tools/bench/bench_session_start.py`: for each vault size, invoke `python skills/parsidion/scripts/session_start_hook.py` via subprocess with a payload cwd, R repetitions (default 5), recording wall time; parse the hook's own `write_hook_event` entry for the per-stage fields it already logs (extend the event payload with stage timings — seed query ms, graph ms, delta ms, semantic ms — if not already present; that extension is part of this enhancement and also improves production observability).
3. **Determinism.** Run with the semantic leg forced to a stub (`search.backend: embeddings` + absent DB → fast documented fallback, or a config knob `session_start_hook.use_embeddings: false` which already exists) so the bench measures the code under this repo's control, not the parsight daemon or model load.
4. **Budget gate.** `make bench-hooks` runs both sizes and asserts: N=500 median < 1.0 s, N=5000 median < 2.5 s (calibrate the exact numbers on first run of the harness; encode them as constants at the top of the driver with a comment on the calibration date/machine). Exit nonzero on breach. Not wired into `make checkall`/CI by default (machine-dependent) — it is an on-demand and pre-release gate; document in CONTRIBUTING.md when to run it.
5. **Trend output.** Append each run's results as a JSON line to `tools/bench/results.jsonl` (gitignored) so local before/after comparisons are one `tail` away.

## Files to touch

- `tools/bench/gen_bench_vault.py`, `tools/bench/bench_session_start.py` (new)
- `skills/parsidion/scripts/session_start_hook.py` + `session_start/context.py` (stage-timing fields in the hook event — small, additive)
- `Makefile` (`bench-hooks` target), `.gitignore` (results.jsonl)
- `CONTRIBUTING.md` (when to run), `docs/ARCHITECTURE.md` (one paragraph in the hook-latency discussion)
- `tests/` (unit test for the generator's vault validity: generated notes pass `update_index.py` cleanly)

## Verification

- `make bench-hooks` runs end-to-end on this machine and prints a per-stage table for both sizes; deliberately slowing the hook (e.g. `PARSIDION_BENCH_SLEEP=3` test hook or a temporary sleep) makes it exit nonzero.
- Generator test passes: `uv run pytest tests/ -k bench -q`.
- Stage-timing fields appear in `hook_events.log` from a normal run and `vault-stats --hooks 5` still renders.
- `make checkall` green (bench itself excluded from the gate).

## Rollback

Delete the `tools/bench/` directory and the Make target; the stage-timing event fields are additive JSON keys with no reader dependency (`vault-stats --hooks` tolerates unknown keys — verify while implementing) and can stay or be reverted independently.
