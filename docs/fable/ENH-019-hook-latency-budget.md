# ENH-019 — Hook latency percentiles and a SessionStart budget alert in `vault-stats --hooks`

> Status: done (2026-08-25; kanban 01a0319f57bd7a31b99fb8d8cf09fb0c)
> Impact: medium · Effort: S

## Goal

Show, per hook, the p50/p95/max `duration_ms` over the recent window and flag when SessionStart
is approaching the 60 s timeout the installer registers, so a slow AI selector or a cold par-mem
daemon is visible before Claude Code starts cancelling the hook.

## Current state

- Every hook appends `{"hook", "ts", "project", "notes_injected", "chars", "duration_ms"}` to
  `<vault>/hook_events.log` via `core/vault_fs.write_hook_event` (`vault_fs.py:380-408`).
- `vault-stats --hooks N` (`cli/stats/operations.py:83` `run_hooks`) prints the last N raw events
  and nothing else; there is no aggregation.
- The SessionStart timeout is 60000 ms unconditionally (`installer/paths.py:71`, 0.20.0). The AI
  selector, graph expansion, and par-mem probe all run inside that budget; the only signal today is
  the hook silently being cancelled.
- `vault_health.py` (ENH-007) already computes a composite score and could consume the same
  aggregate.

## Implementation

1. **Aggregation.** In `cli/stats/operations.py`, add
   `summarize_hook_latency(events, *, window_days=7) -> dict[str, HookLatency]` (stdlib
   `statistics.quantiles`), keyed by hook name: count, p50, p95, max, and `timeouts` (events whose
   `duration_ms` exceeds the registered timeout for that hook; read the timeout map from
   `installer/paths.py` constants re-exported through `core/vault_constants.py` so the skill does
   not import the installer).
2. **Output.** `run_hooks` prints the aggregate table above the raw tail. Add `--hooks-window N`
   (days) and honour `--json` if `vault-stats` has it, else print the table with the existing
   Rich/plain switch (`_get_console` in `cli/stats/_common.py:24`).
3. **Alert.** If SessionStart p95 exceeds 70 % of its timeout, print a one-line warning with the
   three biggest contributors when the events carry stage timings; otherwise the generic warning.
   Optional: have `write_hook_event` accept `stages: dict[str, float]` and have
   `session_start_hook.py` pass `{"ai_select": ms, "graph_expand": ms, "parmem": ms}` (additive,
   older readers ignore the field).
4. **Health.** In `core/vault_health.py`, add a `hook_latency` component (0-100 from the
   SessionStart p95 / timeout ratio) to the composite score with a small weight, and show it in
   `vault-stats --health`.
5. **Dashboard.** `vault-stats --dashboard` includes the latency table.

## Files to touch

- `skills/parsidion/scripts/cli/stats/operations.py`, `cli/stats/cli.py`, `cli/stats/dashboard.py`
- `skills/parsidion/scripts/core/vault_constants.py`, `core/vault_fs.py` (optional `stages`)
- `skills/parsidion/scripts/session_start_hook.py` (optional stage timings)
- `skills/parsidion/scripts/core/vault_health.py`
- `tests/test_stats_operations.py` (new; also closes the QA-015 gap for this module), `tests/test_vault_health.py`
- `CLAUDE.md`, `docs/USAGE.md` (`--hooks` description)

## Verify

- `uv run pytest tests/test_stats_operations.py tests/test_vault_health.py -q` passes with a
  fixture log containing 50 events, including two over 60000 ms, and asserts p95, max, and
  `timeouts == 2`.
- `vault-stats --hooks 20` on the live vault prints the aggregate table followed by 20 raw events.
- With a fixture where SessionStart p95 is 50000 ms, the output contains the budget warning.
- `vault-stats --health --json` includes a `hook_latency` component.
- `make checkall` exit 0.

## Rollback

Additive output; revert the commit. The optional `stages` field in `hook_events.log` is ignored
by older readers and needs no migration.
