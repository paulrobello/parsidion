**Field rules:**
- `date`: The date the note was created or last substantially updated.
- `type`: Must be one of the enumerated values. Drives folder placement.
- `tags`: Freeform but prefer existing tags. Check the vault index first. Use kebab-case, never underscores — mixed underscore/hyphen tags fragment search and dedup across the vault. Convert repo names like `par_ai_core` to `par-ai-core`. Prefer short singular tags — e.g. `voxel` not `voxel-engine`, `hook` not `hooks`, `fractal` not `fractals`. Longer compound tags are acceptable only when the shorter form would be ambiguous.
- `project`: Optional. Must also be kebab-case (no underscores). Convert repo names: `par_ai_core` → `par-ai-core`.
- `confidence`: `high` = verified across multiple interactions or sources. `medium` = likely correct, single source. `low` = hypothesis or unverified.
- `provenance` (optional): Origin of the note's content. `explicit` = user directly stated it; `inferred` (default) = derived from transcript/behavior or distilled by an agent; `corrected` = edited from a prior incorrect version; `observed` = directly witnessed at runtime (e.g. daily notes); `imported` = brought in from an external source. Omit when unsure — defaults to `inferred`.
- `related`: Must contain at least one wikilink in inline quoted array format: `["[[note-one]]", "[[note-two]]"]`. No orphan notes.
