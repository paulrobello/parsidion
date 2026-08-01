---
date: 2026-07-31
type: notetype
tags: [sqlite, locking]
confidence: high
related: ["[[sqlite]]"]
sources: []
---
# SQLite locking fix

Enable WAL mode and use a connection-pool to avoid database-is-locked errors.
The inode is stable across retries so the pool can reconnect safely.
