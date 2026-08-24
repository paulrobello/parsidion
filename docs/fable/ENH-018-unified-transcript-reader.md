# ENH-018 — One byte-bounded transcript reader for every runtime, with huge-line chunking

> Status: not started (filed 2026-08-23, Fable audit cycle)
> Impact: medium · Effort: M · Depends on: ARC-002 (unified session-end pipeline). Related: SEC-022

## Goal

Every hook and adapter reads session transcripts through a single `TranscriptReader` that is
byte-bounded, tolerant of multi-megabyte single lines (large subagent tool results), and returns
already-parsed message records, so the "No result" dead-letter class caused by tail-by-lines
missing huge-line transcripts disappears and the byte bound applies to Codex/Gemini/omp too.

## Current state

- Claude path: `session_stop_hook.py:435,451`, `pre_compact_hook.py:395`,
  `subagent_stop_hook.py:201` call `core/vault_fs.read_last_n_lines(path, n, max_bytes=...)`
  (`vault_fs.py:197`), each with its own config key (`*.transcript_tail_bytes`).
- Adapter path: `agent_adapter.py:546-549` uses `fh.readlines()` on the whole file and `:663`
  calls `read_last_n_lines` without `max_bytes` (SEC-022).
- Summarizer: `summarizer/transcript.py` has its own tail/clean logic with
  `summarizer.transcript_tail_lines` and `transcript_tail_bytes`.
- Memory note `parsidion-summarizer-subagent-failure-mode`: large subagent transcripts produce
  "No result" because a single JSONL line can exceed the whole byte budget, so the tail returns
  zero usable records.

## Implementation

1. **Reader.** New `core/transcript_reader.py` (stdlib only):
   `read_tail(path, *, max_lines, max_bytes, allowlist_roots) -> TranscriptTail` where
   `TranscriptTail` holds `records: list[dict]`, `truncated: bool`, `bytes_read: int`,
   `oversized_lines: int`. Algorithm: seek to `size - max_bytes`, read forward, drop the partial
   first line, parse each line as JSON; for a line longer than `max_line_bytes` (default 256 KiB)
   keep the record but replace string fields longer than 64 KiB with a
   `"<truncated N bytes>"` marker instead of discarding the line. Reuse the path allowlist from
   `core/vault_hooks.py` (transcript roots `~/.claude`, `~/.pi`, `$CODEX_HOME/sessions`,
   `$GEMINI_HOME`).
2. **Adapters.** Give `AgentAdapter` a `transcript_tail(path, opts)` default implementation that
   calls `read_tail`; the Claude, Codex, Gemini, pi, and omp adapters stop reading files
   themselves. `run_session_end` (post-ARC-002) consumes `TranscriptTail.records`.
3. **Summarizer.** `summarizer/transcript.py` uses `read_tail` for the initial load, then its
   existing cleaning; if `oversized_lines > 0`, log it in the progress file so
   `vault-stats --summarizer-progress` shows why a session came back thin.
4. **Config.** One `transcripts` section (`tail_lines`, `tail_bytes`, `max_line_bytes`) with the
   per-hook keys kept as overrides for one release (deprecation warning via `validate_config`).
   Update the schema, template, and CLAUDE.md table (or ENH-017's generator).
5. **Tests.** `tests/test_transcript_reader.py`: 5 MB transcript with a 3 MB single line yields
   records from both sides of it; `max_bytes` smaller than the last line still returns that record
   truncated; path outside the allowlist raises; adapters all route through the reader (assert by
   monkeypatching `read_tail`).

## Files to touch

- new `skills/parsidion/scripts/core/transcript_reader.py`, `skills/parsidion/scripts/transcript_reader.py` (shim)
- `skills/parsidion/scripts/agent_adapter.py`, `session_stop_hook.py`, `pre_compact_hook.py`, `subagent_stop_hook.py`
- `skills/parsidion/scripts/summarizer/transcript.py`
- `skills/parsidion/scripts/core/vault_schema.py`, `templates/config.yaml`, `CLAUDE.md`
- `tests/test_transcript_reader.py`, `tests/test_stdlib_only.py` (add the module), `tests/test_agent_adapter.py`

## Verify

- `uv run pytest tests/test_transcript_reader.py tests/test_agent_adapter.py tests/test_stdlib_only.py -q` passes.
- Replay a known dead-lettered subagent transcript (from `~/ParsidionVault/dead_letters.jsonl`, reason "No result") through
  `env -u CLAUDECODE uv run --no-project skills/parsidion/scripts/summarize_sessions.py --sessions <file> --dry-run`
  and confirm the cleaned transcript is non-empty.
- `grep -rn "readlines()" skills/parsidion/scripts/agent_adapter.py` returns nothing.
- `make checkall` exit 0.

## Rollback

The per-hook config keys still work for one release; revert the commit to restore the old
readers. No on-disk format changes.
