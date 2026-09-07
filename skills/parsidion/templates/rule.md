---
date: {{date}}
type: rule
tags: [rule]
triggers: []
confidence: medium
sources: []
related: []
provenance: inferred
---

## Summary

A rule note carries a directive agents must follow **only when its trigger
fires** — e.g. while working in a repository, on a file kind, or on a topic.
Non-matching contexts never see it.

## Trigger syntax

`triggers` in frontmatter is a list; each entry is one of:

- **Keyword** — lowercase kebab-case, 2-40 chars (`sqlite`, `prompt-cache`).
  Matches whole words: hyphens in the keyword match a hyphen or whitespace,
  so `prompt-cache` fires for "prompt cache". The prompt-submit hook matches
  against the user's prompt; the pre-tool-use hook matches against the file
  path being read/edited.
- **Path pattern** — an fnmatch glob containing `/` or `*`, matched
  case-insensitively against the file path (`*.py`, `skills/`).
- **`always`** — the rule fires for every prompt and every file. Must be the
  only entry.

At least one trigger is required: a rule with empty `triggers` never
injects and is reported by `vault_doctor`'s `rule-triggers` check.
