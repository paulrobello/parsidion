## Vault Doctor

`vault_doctor.py` scans all vault notes for structural issues and repairs repairable issues via the configured prompt AI backend.

### Issues detected

| Code | Severity | Description |
|---|---|---|
| `MISSING_FRONTMATTER` | error | No YAML frontmatter block |
| `MISSING_FIELD` | error | `date`/`type` missing (all notes); `confidence`/`related` missing (non-daily) |
| `INVALID_TYPE` | error | `type` not in allowed set |
| `INVALID_DATE` | warning | `date` not in YYYY-MM-DD format |
| `ORPHAN_NOTE` | warning | No `[[wikilinks]]` in `related` field |
| `BROKEN_WIKILINK` | warning | Link target not found in vault |
| `HEADING_MISMATCH` | warning | No `#` heading found; first `##` heading should be promoted to `#` (skips daily notes) |
| `FLAT_DAILY` | warning | `Daily/YYYY-MM-DD.md` instead of `Daily/YYYY-MM/DD-{username}.md` |
| `PREFIX_CLUSTER` | warning | 3+ flat notes share a kebab prefix — should be moved into a subfolder |
| `NESTED_FM_KEY` | warning | Indented mapping key; the parser reads it as a top-level scalar |
| `UNTERMINATED_FM_LIST` | error | Inline list opens with `[` but never closes on the same line |
| `ORPHAN_FM_BRACKET` | warning | Stray `]` left as the first body line |
| `SCALAR_LIST_FIELD` | warning | `tags`/`related`/`sources` holds a bare scalar, so its entries are lost |
| `DUPLICATE_FM_KEY` | warning | Same top-level key twice; the parser is last-wins and drops the earlier value |

Daily notes (`type: daily` or path under `Daily/`) are exempt from `confidence`, `related`, and orphan checks.

The last five codes are **frontmatter-syntax** checks. `parse_frontmatter` implements a
deliberately small YAML subset and never raises, so a note using a shape outside it
silently gets the wrong value — these read the raw frontmatter text, the only place the
damage is still visible.

### Running the doctor

```bash
# Scan and report only (no writes)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --dry-run

# Fix everything in one pass (frontmatter + tags + subfolders) — used by nightly cron
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all

# Individual fix modes:
# Repair frontmatter issues via the configured prompt AI backend (3 parallel workers by default)
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter --jobs 5 --timeout 180

# Detect and merge duplicate tags (plural/singular, hyphen/underscore, collapsed)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags           # dry-run
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-tags --execute # apply

# Detect and migrate prefix clusters into subfolders
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders           # dry-run
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --migrate-subfolders --execute # apply

# Repair up to 20 notes at a time
env -u CLAUDECODE uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-frontmatter --limit 20

# Errors only (skip warnings)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --errors-only --dry-run

# List every selectable rule with its risk column
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --list-rules

# Safe bulk invocation: fix-all without the risky bulk rules (ENH-015)
uv run --no-project ~/.claude/skills/parsidion/scripts/vault_doctor.py --fix-all --skip strip-prefixes --skip subfolder-prefix
```

For Claude CLI backend calls launched from inside Claude Code, unset `CLAUDECODE` as shown above. Codex backend calls use `codex exec` with an internal recursion guard.

`--fix-all` is equivalent to `--fix-frontmatter --fix-tags --strip-prefixes --migrate-subfolders --migrate-daily-notes --fix-permissions --execute`. Note that `--strip-prefixes` performs a vault-wide bulk file rename (rewriting wikilinks across every note) and `--migrate-daily-notes` renames legacy flat dailies to `DD-{username}.md` — run `--fix-all` on a clean git tree so a rename can be reverted. The nightly cron via `summarize_sessions.py --run-doctor` uses `--fix-all`.

**Per-rule selection (ENH-015).** `--only RULE` / `--skip RULE` (repeatable, mutually exclusive) select rules by name from the `--list-rules` catalog, covering both the per-note checks and the fix modes: `frontmatter-syntax`, `required-fields`, `valid-type`, `date-format`, `related-links`, `self-ref`, `headings`, `broken-wikilinks`, `flat-daily` (checks); `frontmatter-repair` (the AI repair stage), `tags`, `strip-prefixes`, `subfolder-prefix`, `daily-namespace`, `permissions` (bulk/safe modes). Selection gates the fix-mode dispatch, the scan checks, and the in-scan prefix-cluster stage alike. Every scan run ends with a per-rule `rule | found | fixed | skipped` report; `skipped` counts issues left unprocessed (under `--dry-run`, all of them). The `bulk` risk label marks the historically regression-prone rules (bulk renames, tag merges, AI body rewrites); the recommended safe bulk invocation is `--fix-all --skip strip-prefixes --skip subfolder-prefix`.

Repairs run in parallel (`--jobs N`, default 3). Each prompt AI subprocess (`claude -p` or `codex exec`) is independent so parallelism is safe; state updates and console output are guarded by a lock so lines are never interleaved. The per-call timeout (`--timeout SECS`) defaults to 120s — increase it when running many parallel workers to avoid spurious timeouts.

Repairable codes (prompt AI backend can fix): `MISSING_FRONTMATTER`, `MISSING_FIELD`, `INVALID_TYPE`, `INVALID_DATE`, `ORPHAN_NOTE`.
Auto-repairable without prompt AI (Python-only): `BROKEN_WIKILINK` (exact stem match, prefix-strip match, then a `vault-search` semantic fallback that never returns a `Daily/` note — a journal page matches almost any topic, so substituting one satisfies "the link resolves" while destroying what the link meant; when nothing suitable is found the link is dropped instead), `DUPLICATE_TAG` (merge via `--fix-tags`), `HEADING_MISMATCH` (promotes first `##` to `#`; enabled by default, disable with `--no-fix-headings`).
Auto-repairable via Python + prompt AI filter: `PREFIX_CLUSTER` — candidates are detected by Python; generic modifier prefixes that would split a compound slug are blocked deterministically by `_GENERIC_PREFIX_DENYLIST` (`client`/`code`/`env`/`id`/`admin`/`asset` + common modifiers); then the configured small model filters remaining generic-word false positives (e.g. 'fixing', 'missing'), keeping only specific subject names (project, library, OS, tool). Files are then moved and wikilinks patched by Python.
Auto-repairable without prompt AI (Python-only deterministic pre-pass): `NESTED_FM_KEY` (the `metadata:` mapping-wrapper shape — flattened to top level; other indented-key shapes still report) and `SCALAR_LIST_FIELD` (scalar `tags`/`sources` whitespace-split into a list; scalar `related` containing `[[wikilinks]]` re-wrapped). These run before issue classification; no AI backend call.
Not auto-repairable (require manual fix): `FLAT_DAILY`, and the three remaining frontmatter-syntax
codes (`UNTERMINATED_FM_LIST`, `ORPHAN_FM_BRACKET`, `DUPLICATE_FM_KEY`). Those syntax codes are
detection-only by design — the AI repair path is deliberately not turned loose on structurally
broken frontmatter. A note carrying one is left out of `doctor_state.json` entirely so every run
re-reports it until it is fixed (a `skipped` state entry is permanent, unlike `ok`, which expires
after `STATE_STALE_DAYS`).

### Singleton guard

Only one doctor may run at a time. The doctor claims an `flock` on `<vault>/.doctor.lock` (SEC-016); a second doctor finds the lock held, prints an error, and exits. The kernel releases the lock when the holder exits — including on SIGKILL — so there is no stale-PID state to recover from.

### Auto-commit stale files

Before scanning, the doctor runs `git status --porcelain` on the vault and commits any tracked or untracked files whose mtime is ≥ 15 minutes old. Deletions are skipped (no mtime to check). The commit message is `chore(vault): auto-commit N stale file(s) via vault_doctor`. This is a no-op when the vault has no `.git` directory or `git.auto_commit` is `false` in config. With `--dry-run` the files are listed but not committed.

### State tracking

The doctor writes `<resolved vault>/doctor_state.json` to avoid reprocessing notes unnecessarily:

| Status | Meaning | Next run behaviour |
|---|---|---|
| `ok` | No issues found | Skipped for 7 days |
| `fixed` | Prompt AI backend repaired it | Re-checked to confirm |
| `failed` | Prompt AI backend returned no output | Retried |
| `timeout` | Prompt AI backend timed out once | Retried once more |
| `needs_review` | Timed out on retry | Skipped — user must fix manually |
| `skipped` | Only non-repairable issues | Skipped indefinitely |

Use `--no-state` to ignore the state file and rescan all notes.

Run `update_index.py` after repairs — it reads `doctor_state.json` and adds a vault health line to `CLAUDE.md`'s Quick Stats section.

