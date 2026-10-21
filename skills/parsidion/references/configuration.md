## Configuration

All hooks and the summarizer read `<resolved vault>/config.yaml` for settings.
Precedence: **hardcoded defaults → config.yaml → CLI args** (last one wins).

A template with all options lives at `~/.claude/skills/parsidion/templates/config.yaml`.
Copy it to the vault root to get started:

```bash
cp ~/.claude/skills/parsidion/templates/config.yaml ~/ParsidionVault/config.yaml
```

### Config Sections

> **📝 Note:** Model IDs shown in the config block below (e.g. `claude-sonnet-4-6`,
> `claude-haiku-4-5-20251001`, `gpt-5.6-luna`/`gpt-5.6-terra`, `BAAI/bge-small-en-v1.5`) are the hardcoded defaults.
> They can be changed via the corresponding keys in `<resolved vault>/config.yaml` without
> modifying any scripts. See the template at
> `~/.claude/skills/parsidion/templates/config.yaml` for all available keys.

```yaml
session_start_hook:
  ai_model: null           # Model for AI note selection (null = disabled)
  max_chars: 4000          # Max context injection characters
  ai_timeout: 25           # AI call timeout in seconds
  recent_days: 3           # Days to look back for recent notes
  debug: false             # Append injected context to debug log in $TMPDIR
  verbose_mode: false      # If true, inject full note summaries instead of compact one-line index
  use_embeddings: true     # Blend semantic matches into context; graceful fallback if db absent
  show_dead_letter_notice: false  # Include the dead-letter queue warning in SessionStart context

session_stop_hook:
  ai_model: null           # Model for AI classification (null = disabled)
  ai_timeout: 25           # AI call timeout in seconds
  auto_summarize: true     # Auto-launch summarizer when pending entries exist
  transcript_tail_lines: 200      # Lines to inspect by default
  pi_transcript_tail_lines: 1000  # Deeper fallback tail for pi transcripts when needed

subagent_stop_hook:
  enabled: true            # Set false to disable subagent transcript capture entirely
  min_messages: 3          # Minimum assistant message count (pi transcripts default to 1 when unset)
  excluded_agents: "vault-explorer,research-agent"  # Never capture these

pre_compact_hook:
  lines: 200               # Transcript lines to analyse

summarizer:
  model: null          # null = ai_models.<backend>.large
  max_parallel: 5          # Concurrent summarization tasks
  transcript_tail_lines: 400
  max_cleaned_chars: 12000
  persist: false           # Legacy no-op; retained for CLI compatibility
  cluster_model: null  # null = ai_models.<backend>.small
  dead_letter_retention_days: 7  # Prune dead_letters.jsonl entries older than N days each run (<=0 disables)

ai:
  backend: auto            # auto | claude-cli | codex-cli | none

ai_models:
  claude:
    small: claude-haiku-4-5-20251001
    large: claude-sonnet-4-6
  codex:
    small: gpt-5.6-luna
    large: gpt-5.6-terra

codex_cli:
  command: codex
  timeout: 60
  sandbox: read-only
  ephemeral: true
  skip_git_repo_check: true
  suppress_notify: true

embeddings:
  model: BAAI/bge-small-en-v1.5  # fastembed model ID; ~67 MB ONNX model, cached after first run
  min_score: 0.45                 # Minimum cosine similarity for search results
  top_k: 10                       # Default result count

git:
  auto_commit: true        # Auto-commit vault changes after writes
```

- `auto` prefers the active runtime when detectable and falls back to Claude CLI when ambiguous.
- Codex backend uses `codex exec` with default ephemeral/read-only/skip-git-repo-check/output-last-message prompt calls and the normal Codex CLI auth path; Parsidion does not manage Codex auth files such as `~/.codex/auth.json`. Internal calls also pass `--config notify=[]` by default so user-configured Codex turn-complete notifications do not fire for vault summarization. This is not OpenAI API-key provider support. Gemini runtime hooks do not imply `ai.backend: gemini`; no Gemini prompt backend is provided yet.
- `summarize_sessions.py` also uses the configured prompt AI backend. Claude uses `claude -p`, Codex uses `codex exec`, and no Claude Agent SDK or Codex SDK is required for this path.

### Programmatic Access

```python
import vault_common

# Load all config (cached per process)
config = vault_common.load_config()

# Get a single value with fallback
max_chars = vault_common.get_config("session_start_hook", "max_chars", 4000)
```

If `config.yaml` is missing or unreadable, all `get_config()` calls return the default.

