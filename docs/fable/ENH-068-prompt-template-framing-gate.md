# ENH-068 — Prompt-template injection-framing gate

## Goal

Make the SEC-108/SEC-115 untrusted-data framing convention executable: a gate that fails
CI when any prompt template in `skills/parsidion/templates/prompts/` inlines untrusted
content (transcripts, note bodies, vault content) without the SYSTEM framing markers its
siblings carry. The 2026-11-13 audit found the convention held by hand — and had already
missed two templates (`summarize-chunk.md`, `repair-frontmatter.md`, filed as SEC-001 /
SEC-002). This enhancement prevents the next gap from needing an audit to find.

## Current-state context

- Every AI-backend prompt template lives in `skills/parsidion/templates/prompts/`
  (`summarize-session.md`, `summarize-chunk.md`, `merge-notes.md`, `select-notes.md`,
  `detect-conflicts.md`, `repair-frontmatter.md`, plus any added since).
- Framing style: a `SYSTEM:` preamble declaring the inlined block UNTRUSTED DATA plus
  explicit delimiters around the inlined variable (see `summarize-session.md:8-11`).
- Templates carry frontmatter (`id`, `version`, `syntax`, `variables`, `description`);
  `syntax: template` uses `$var` substitution, `syntax: format` uses `{var}`.
- The loader (`prompt_templates.py`, ENH-008) validates variables but nothing about
  framing; existing template-content tests pin individual templates ad hoc.
- SEC-001's fix adds the contract test for `summarize-chunk.md` only; this enhancement
  generalizes it across the whole directory and wires it into the standard gate.

## Implementation

1. Land SEC-001 and SEC-002 first (they close the known gaps; the gate then enforces the
   corrected baseline).
2. New module `tests/prompt_framing_gate.py` (or a plain test module
   `tests/test_prompt_framing_gate.py` — preferred, no new harness):
   - Enumerate every template file in `skills/parsidion/templates/prompts/` (the directory
     is the contract — a new template is automatically covered).
   - Maintain an explicit allowlist of (template id, variable) pairs that are NOT
     untrusted (e.g. `summarize-session`'s `$today`, `$cats_str`, `$valid_types`,
     `$tags_instruction`, `$session_id`, `$dedup_block` scaffolding, `repair-frontmatter`'s
     `{rel}`, `{issue_lines}`, `{valid_types}`, `{related_rule}`, `{candidate_section}`).
     Everything unlisted that renders *content* (transcripts, note bodies) is untrusted.
   - For each untrusted variable, assert its template contains: (a) an
     `UNTRUSTED DATA` SYSTEM preamble line, and (b) a delimiter pair wrapping the variable
     (the variable reference appears between two identical custom markers).
   - Determination of "content" variables: declaration list in the test with a comment per
     entry naming what flows in (transcript text / note body / vault note). Reviewer-visible,
     cheap to maintain.
3. Wire nothing new into `make checkall` — a standard pytest module under `tests/` is
   already in the `test` leg. No Makefile change needed.
4. Docs: one paragraph in `docs/DOCUMENTATION_STYLE_GUIDE.md` (or CONTRIBUTING.md) stating
   the rule: any template variable carrying transcript/note/vault content must be SYSTEM-
   framed and delimited; the gate enforces it; allowlist entries need a justification
   comment.

## Files to touch

- `tests/test_prompt_framing_gate.py` (new)
- `docs/DOCUMENTATION_STYLE_GUIDE.md` or `CONTRIBUTING.md` (rule paragraph)
- No changes to `prompt_templates.py` or the templates themselves (post-SEC-001/002)

## Verification

- `uv run pytest tests/test_prompt_framing_gate.py -q` green on the corrected baseline.
- Negative test in-suite: append a synthetic template with an unframed content variable to
  a tmp copy of the prompts dir and assert the gate fails (proves the gate can fail).
- `make checkall` green; CI runs the suite unchanged.

## Rollback

Delete the test module. No runtime surface touched — templates, loader, and hooks are
unmodified, so rollback is trivial and risk-free.
