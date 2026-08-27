# Prompt Templates & Evaluation

How Parsidion's AI prompts are authored, versioned, and scored. Covers the six externalized
prompts under `skills/parsidion/templates/prompts/`, the strict-variable loader that renders
them, and the eval harness that measures a prompt edit's effect on vault-note quality before
the edit lands.

## Table of Contents

- [Overview](#overview)
- [Template Format](#template-format)
  - [The Strict Variable Contract](#the-strict-variable-contract)
  - [Two Substitution Syntaxes](#two-substitution-syntaxes)
- [The Six Prompts](#the-six-prompts)
- [Single Source for the Note Schema](#single-source-for-the-note-schema)
- [Prompt Version Stamping](#prompt-version-stamping)
- [Running an Evaluation](#running-an-evaluation)
  - [Scoring Rubric](#scoring-rubric)
  - [Cost Controls](#cost-controls)
  - [Result Caching](#result-caching)
- [Adding a Golden Case](#adding-a-golden-case)
  - [Anonymization Requirement](#anonymization-requirement)
- [Version Bump Rules](#version-bump-rules)
- [Files](#files)

## Overview

Before ENH-008, Parsidion's prompts lived inline as string literals scattered across
`summarize_sessions.py`, `vault_doctor.py`, `vault_merge.py`, `vault_conflicts.py`, and
`session_start_hook.py`. The note-type enum was restated in two vocabularies kept in sync
only by a parity test — the original ARC-010 bug (the summarizer silently dropping the
`knowledge` type) happened because of exactly that duplication.

The prompts are now externalized template files with YAML frontmatter, rendered through a
single strict-variable loader, and the note-type set is stated once in `note_schema`. Each
prompt carries a semantic version that is stamped into every AI-generated note's frontmatter,
so the `note_index.prompt_version` column can slice note quality by the prompt that produced
it. A golden-transcript eval harness scores prompt edits against expected characteristics
rather than vibes.

## Template Format

Each template lives at `skills/parsidion/templates/prompts/<id>.md` with this shape:

```markdown
---
id: summarize-session
version: 1.0.0
syntax: format
variables: [project, cats_str, today, dedup_block, cleaned_transcript, ...]
description: Generate a structured vault note from a cleaned session transcript.
---
<the prompt body with {variable} placeholders>
```

The frontmatter fields:

| Field         | Required | Purpose                                                       |
|---------------|:--------:|---------------------------------------------------------------|
| `id`          | yes      | Canonical id; matches the filename stem.                      |
| `version`     | yes      | Semantic version (see [Version Bump Rules](#version-bump-rules)). |
| `variables`   | yes      | Declared variable names; enforced bidirectionally (see below). |
| `syntax`      | no       | `format` (default, `str.format`-style) or `template` (`string.Template`). |
| `description` | yes      | One-line summary for docs and the `--prompt` chooser.         |

### The Strict Variable Contract

`prompt_templates.render(prompt_id, **variables)` raises `PromptError` when:

- a **declared** variable is missing from the call (the classic silent-empty substitution that
  hides regressions), or
- an **undeclared** variable is passed (catches caller typos like `projectt=`).

Both checks run against the template's declared `variables` list, so a template and its callers
cannot drift unnoticed. This is the load-bearing part of the loader — a missing variable that
silently renders as empty is precisely how a prompt degrades without anyone noticing.

### Two Substitution Syntaxes

- `syntax: format` (default) — `str.format`-style `{var}` placeholders. Literal braces in the
  body must be escaped as `{{` / `}}` (`detect-conflicts` escapes its JSON output example this
  way). Used by the four non-summarizer prompts.
- `syntax: template` — `string.Template` `$var` placeholders, so literal `{` / `}` need no
  escaping (`summarize-session`'s body contains a JSON example). Used by the two legacy
  summarizer prompts.

The loader handles both via the `syntax` frontmatter field; consumers call the same `render()`
regardless.

## The Six Prompts

| Id                   | File                          | Consumer                                             |
|----------------------|-------------------------------|------------------------------------------------------|
| `summarize-session`  | `summarize-session.md`        | `summarize_sessions.py` — main note-writing prompt   |
| `summarize-chunk`    | `summarize-chunk.md`          | `summarize_sessions.py` — hierarchical chunk summary |
| `repair-frontmatter` | `repair-frontmatter.md`       | `vault_doctor.py` — frontmatter repair               |
| `merge-notes`        | `merge-notes.md`              | `vault_merge.py` — two-note merge                    |
| `detect-conflicts`   | `detect-conflicts.md`         | `vault_conflicts.py` — contradiction detection       |
| `select-notes`       | `select-notes.md`             | `session_start_hook.py` — `--ai` note selector       |

Every prompt is held to the byte-identical rendering gate in
`tests/test_prompt_templates.py` (`-k identical`): the rendered template must equal the
pre-externalization inline output byte-for-byte. A template edit that drifts the rendering
fails the gate.

## Single Source for the Note Schema

`skills/parsidion/scripts/note_schema.py` is the single source for:

- `VALID_NOTE_TYPES` — every valid `type` frontmatter value.
- `TYPE_FOLDERS` — type → vault folder routing.
- `REQUIRED_FRONTMATTER_FIELDS`, `REQUIRED_KNOWLEDGE_FIELDS`, `VALID_PROVENANCE_VALUES`,
  `VALID_CONFIDENCE_VALUES`.
- `TAG_RULES` — the kebab-case / short-singular tag rule, interpolated by every prompt that
  instructs the model on tags.
- `NOTE_TYPES_DISPLAY` — the pre-computed comma-separated type list prompts interpolate as
  `{note_types}`.

The summarizer and `vault_doctor` re-export these under their legacy private names
(`_VALID_NOTE_TYPES`, `VALID_TYPES`, etc.) so every existing call site keeps working — but they
are aliases over the same object, not copies. The guard test
`test_all_consumers_share_one_note_type_set` asserts identity, so the ARC-010 bug class (two
frozensets silently drifting apart) is now impossible at the source.

## Prompt Version Stamping

Every AI-generated note gets a `prompt_version: <id>@<semver>` field in its frontmatter (beside
`session_id`), stamped by `_stamp_prompt_version` in `summarizer/notes.py`. The
`note_index.prompt_version` column (added by `update_index.py` / `ensure_note_index_schema`)
lets evaluation slice note quality by the prompt that produced it. Older notes and older
summarizer versions simply have an empty `prompt_version` — the field is additive and ignored
by older code.

## Running an Evaluation

The eval harness lives at `tools/eval/prompt_eval_run.py`. It is **opt-in** — `make checkall`
does not invoke it, by design. Run it by hand:

```bash
# Score the current summarizer prompt against the golden set (small model tier):
uv run tools/eval/prompt_eval_run.py --prompt summarize-session

# Explicit larger model (opt-in for higher cost):
uv run tools/eval/prompt_eval_run.py --prompt summarize-session --model claude-sonnet-4-5

# Quick check on the first 3 cases:
uv run tools/eval/prompt_eval_run.py --prompt summarize-session --limit 3

# Ignore the cache and re-run every case:
uv run tools/eval/prompt_eval_run.py --prompt summarize-session --no-cache
```

The `--prompt` id selects among six evaluators (one per externalized prompt), each a module
under `tools/eval/evaluators/` named for the prompt id with underscores
(`summarize-session` → `summarize_session.py`). The harness renders the prompt through the real
`prompt_templates` loader (the same path production uses), calls the configured AI backend,
and scores the output against the case's `expected.yaml`.

### Scoring Rubric

Each prompt has its own rubric in its evaluator module (weights always sum to 100). The `summarize-session` rubric — the one the examples above exercise — is:

| Check                       | Weight | How                                            |
|-----------------------------|:------:|------------------------------------------------|
| Write-gate decision correct | 25     | matches `should_produce_note`                  |
| Note type correct           | 20     | matches `expected_type`                        |
| Frontmatter valid           | 20     | `note_schema` validator                        |
| Tag precision/recall        | 20     | against `expected_tags_include` / `_exclude`   |
| Required content present    | 15     | `must_mention` substring checks                |

A result file is written to `tools/eval/results/prompt-eval-<id>-<timestamp>.json`
(matching the `embed_eval` harness's output convention).

### Cost Controls

- Defaults to the **small** model tier.
- Prints the **projected AI call count** before starting and asks for confirmation when it
  reaches the threshold (default 12 uncached calls; override with `--yes`).
- **Caches** each case's result keyed by `(prompt_id, version, model, case_id)` under
  `~/.cache/parsidion/prompt-eval/`, so re-running after editing one case does not re-bill the
  rest.

### Result Caching

Each cached result is a JSON file named
`<prompt_id>-<version>-<model>-<case_id>-<hash>.json`. Delete the cache directory to force a
full re-run, or pass `--no-cache` for a single uncached run.

## Adding a Golden Case

Golden cases live under `tests/fixtures/prompts/golden/<prompt_id>/` — one subdirectory per
prompt, because each prompt has a different variable contract and a different rubric. The
evaluator's `load_cases()` discovers every `*.expected.yaml` in that subdir.

Each case is a small group of files sharing a `<NNN>-<short-description>` stem (three-digit
number, kebab-case description). The `.expected.yaml` is always present; the input files are
the prompt's render variables, named `<stem>.<variable>.md`:

| Prompt              | Input fixtures                                                   |
|---------------------|------------------------------------------------------------------|
| `summarize-session` | `<stem>.transcript.md`                                           |
| `summarize-chunk`   | `<stem>.chunk_text.md`                                           |
| `select-notes`      | `<stem>.candidates_text.md`                                      |
| `merge-notes`       | `<stem>.body_a.md` + `<stem>.body_b.md`                          |
| `repair-frontmatter`| `<stem>.content.md` (other vars derived from `expected.yaml`)    |
| `detect-conflicts`  | `<stem>.note_block.md`                                           |

The expected YAML fields:

```yaml
should_produce_note: true          # the write-gate decision the case expects
expected_type: debugging           # the 'type' frontmatter value
expected_tags_include: [sqlite]    # tags the note SHOULD use (recall)
expected_tags_exclude: [misc]      # tags the note SHOULD NOT use (precision)
must_mention: ["WAL", "inode"]     # substrings the note body must contain
frontmatter_valid: true            # whether the frontmatter should parse
related_links_min: 1               # minimum [[wikilink]] count in 'related'
```

Aim for 8-12 cases spanning the distribution the summarizer actually sees: debugging sessions,
refactors, transient runs that should be skipped, knowledge distillation, research, tool
design, framework fixes, project work. Include at least one `should_produce_note: false` case
so the write-gate decision is exercised.

### Anonymization Requirement

Golden transcripts are derived from real sessions and are treated as sensitive by default.
**Strip every identifier before committing:** absolute home paths (`/Users/<name>`),
`$HOME` / `~` expansions, hostnames (`.local`, `.internal`), usernames, and anything resembling
a credential. Use placeholder paths like `/workspace/project` or `/vault/...`.

This is enforced by a test, not by discipline: `tests/test_golden_fixtures_anonymization.py`
scans every fixture for leakage signatures and fails on the first hit. The test also checks the
maintainer's own usernames, so a self-referential leak is caught too.

## Version Bump Rules

Bump the `version` frontmatter field when editing a template:

- **patch** (`1.0.0` → `1.0.1`): wording changes that do not alter the output schema or add
  guidance (e.g. rephrasing a sentence for clarity).
- **minor** (`1.0.0` → `1.1.0`): added guidance, new instructions, or a new variable (the
  model's output shape is unchanged but the prompt is more capable).
- **major** (`1.0.0` → `2.0.0`): changed output schema (e.g. a new required frontmatter field,
  or a changed response format). Existing notes stamped with the old version remain valid —
  `prompt_version` is descriptive, not a compatibility gate.

After any bump, run the eval harness against the golden set and record the before/after mean
score in the commit message.

## Files

| Path | Purpose |
|------|---------|
| `skills/parsidion/scripts/prompt_templates.py` | Stdlib-only loader: `load_prompt`, `render`, strict-variable checking. |
| `skills/parsidion/scripts/note_schema.py` | Single source for `VALID_NOTE_TYPES`, `TYPE_FOLDERS`, tag rules. |
| `skills/parsidion/templates/prompts/*.md` | The six externalized prompt templates. |
| `skills/parsidion/scripts/summarizer/prompt.py` | `build_prompt` + tag/dedup renderers (uses the loader). |
| `skills/parsidion/scripts/summarizer/notes.py` | `_stamp_prompt_version` — injects the version stamp. |
| `tools/eval/prompt_eval_run.py` | Opt-in eval harness (PEP 723 script). |
| `tools/eval/evaluators/_base.py` | Shared `BaseEvaluator`: flat-YAML parser, golden-case discovery, `ScoredCase`. |
| `tools/eval/evaluators/<prompt with underscores>.py` | One evaluator per prompt (e.g. `summarize_session.py`): render / parse / score against `expected.yaml`. |
| `tests/test_prompt_templates.py` | Byte-identical rendering gate + loader contract + ARC-010 convergence. |
| `tests/test_note_index_prompt_version.py` | `note_index.prompt_version` column + migration. |
| `tests/test_golden_fixtures_anonymization.py` | Golden-set anonymization gate. |
| `tests/fixtures/prompts/golden/<prompt_id>/` | Per-prompt golden cases: input fixtures + `expected.yaml`. |
