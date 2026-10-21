**Process a single explicit file** (useful for testing one entry):
```bash
env -u CLAUDECODE uv run ~/.claude/skills/parsidion/scripts/summarize_sessions.py \
  --sessions /path/to/file.jsonl
```

### Options

| Flag | Description | Config key |
|---|---|---|
| `--sessions FILE` | Process an explicit JSONL file instead of the default pending queue | — |
| `--dry-run, -n` | Preview what would be written without creating notes | — |
| `--model MODEL` | Explicit large-model override (default: backend large model) | `summarizer.model` |
| `--persist` | Accepted for backwards compatibility; currently unused | `summarizer.persist` |
| `--retry-dead-letters` | Re-queue retryable dead-lettered sessions into the pending queue, then summarize. Removes them from `dead_letters.jsonl` so they are actually re-processed (the `_dead_lettered_ids` guard otherwise skips them). Most `no_result` dead-letters are transient and succeed on a later retry; the 3-strike graveyard otherwise loses them | — |
| `--reason PREFIX` | With `--retry-dead-letters`: re-queue entries whose `last_failure` starts with this prefix (default `no_result` covers the legacy opaque kind and `no_result_timeout`/`empty`/`backend`) | — |
| `--min-age-days N` | With `--retry-dead-letters`: only re-queue entries dead-lettered at least N days ago (cooldown; default 0 = all matching) | — |
| `--max-count N` | With `--retry-dead-letters`: cap the number re-queued (0 = no cap) | — |

Uses the configured prompt AI backend: Claude runs through `claude -p`, Codex runs through `codex exec`, and no Claude Agent SDK or Codex SDK is required for this summarizer path.
Processes up to 5 sessions in parallel with `anyio` (configurable via `summarizer.max_parallel`).
Rebuilds the vault index automatically when done.
Sessions from today whose generated note type is `daily` are skipped (today's daily note
is still being built by the stop hook).

### Write-Gate Filter

Before generating a note, the summarizer asks Claude to evaluate whether the session contains
reusable insight. Transient sessions (failed experiments, routine builds, dead-ends with no
generalizable lesson) are skipped rather than saved. Skipped sessions are reported separately
from failures in the output summary.

### Hierarchical Summarization

For sessions whose cleaned transcript exceeds `max_cleaned_chars` (default 12,000), the
summarizer splits the transcript into chunks and uses the backend small model by default
(configurable via `summarizer.cluster_model`) to summarize each chunk first. The chunk summaries
are then fed to the main backend large-model note generator. This preserves context from very
long sessions that would otherwise be truncated.

### Automated Backlinks

After writing a new note, the summarizer scans existing vault notes for tag overlap and
injects bidirectional `[[wikilinks]]` — updating both the new note's `related` field and
adding back-references in matching existing notes. This builds the link graph automatically
rather than requiring manual maintenance.

