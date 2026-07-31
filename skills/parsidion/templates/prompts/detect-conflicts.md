---
id: detect-conflicts
version: 1.0.0
syntax: format
variables: [note_count, note_block]
description: Find contradictions between semantically-similar vault notes (vault-conflicts). Note bodies are inlined as untrusted data.
---
You are a knowledge-vault consistency auditor. Below are {note_count} notes that are semantically similar and may overlap.

NOTES:
{note_block}

Identify CONTRADICTIONS ONLY — pairs of notes making conflicting, mutually-exclusive claims about the same subject. Do NOT flag near-duplicates, complements, or unrelated notes sharing keywords.

Respond with ONLY a JSON array (no prose). Each element:
{{"type":"contradiction","a":"<stem A>","b":"<stem B>","a_says":"<one-line claim>","b_says":"<one-line claim>","recommendation":"keep_a|keep_b|merge|needs_review"}}

If there are no contradictions, respond with: []