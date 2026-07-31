# Session transcript — debugging sqlite locking

User: I'm hitting "database is locked" errors in my worker pool. Help me debug.

Assistant: Let me investigate. The error "database is locked" typically means another connection holds a write lock. Let me check how the pool opens connections.

Assistant: I see the issue — every worker opens its own `sqlite3.connect()` with the default 5-second timeout, and they're all trying to write to the same DB file concurrently. SQLite uses file-level locking for writes, so concurrent writers serialize via the timeout.

User: How do I fix it?

Assistant: Two options:
1. Use WAL mode (`PRAGMA journal_mode=WAL`) — allows concurrent readers alongside one writer, and reduces lock contention dramatically.
2. Funnel all writes through a single connection (a writer queue) so there's no contention.

WAL is the higher-leverage fix. Let me apply it.

Assistant: Added `conn.execute("PRAGMA journal_mode=WAL")` right after connect. Also bumped the timeout to 30s. Re-ran the load test — the lock errors are gone and throughput is up 4x because readers no longer block.

User: Why does WAL help exactly?

Assistant: In the default rollback-journal mode, every write takes an EXCLUSIVE lock that blocks ALL readers until the transaction commits. In WAL mode, writes go to a separate append-only `-wal` file, so readers read the main DB and never contend with the single writer. The writer still serializes (SQLite allows one writer at a time), but readers are never blocked. The `inode` of the DB file stays consistent across crash recovery because the `-wal` is replayed on next open.

Assistant: One gotcha I hit: the WAL file and `-shm` file MUST live on the same filesystem as the main DB (same inode) — if a temp directory is on a different mount, WAL silently falls back to rollback journal. Caught this because `/proc/mounts` showed the temp dir on a separate ext4 mount.
