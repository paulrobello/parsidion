### Rebuilding the Index

The `CLAUDE.md` at the vault root is auto-generated. Rebuild it when:
- The user requests it ("rebuild vault index", "update vault index", "refresh vault index", "refresh the vault", "sync the vault")
- After creating, renaming, moving, or deleting notes
- The index timestamp is older than 24 hours

```bash
uv run ~/.claude/skills/parsidion/scripts/update_index.py
```

This scans all vault folders, reads frontmatter, and produces:
- A lean root `CLAUDE.md` index with quick stats, conventions, recent activity, top-20 tags, and folder counts with MANIFEST pointers
- A `TAGS.md` file with the full tag cloud and machine-readable tag list (used by the summarizer to avoid duplicating tags)
- **Per-folder `MANIFEST.md` files** — table-format indexes (Note | Tags | Summary) written inside each subfolder for quick orientation without loading the full root index
- **Staleness markers** — notes with zero incoming wikilinks AND older than 30 days are flagged `[STALE?]` in the index (surfaced for review, never auto-deleted)

When `git.auto_commit` is enabled, the rebuild commits only the generated
`CLAUDE.md`, `TAGS.md`, and `MANIFEST.md` files. Any unrelated work that was
already staged remains staged and is not included in the index commit.

Confirm to the user when the rebuild is complete.
