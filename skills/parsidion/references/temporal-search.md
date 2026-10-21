## Temporal Search

`vault-search` accepts two temporal filters (used with metadata mode, no positional query):

- `--changed-since YYYY-MM-DD` — notes whose file `mtime` is on/after the date (catches edits).
- `--as-of YYYY-MM-DD` — notes whose frontmatter `date` is on/before the date (creation-time bound).

```bash
vault-search --changed-since 2026-06-01   # everything touched since June 1
vault-search --as-of 2026-01-01           # everything dated Jan 1 or earlier
vault-search --changed-since 2026-06-01 --tag python   # combine with other filters
```

