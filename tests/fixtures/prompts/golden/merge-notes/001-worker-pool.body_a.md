## Summary
Worker-pool tuning for the ingestion pipeline. Enable WAL mode on the SQLite
database so concurrent readers stay unblocked while the writer flushes batches
to disk.

## Key Learnings
- Use a single writer thread to avoid database lock contention
- Bump the pool size to 4 workers for throughput
- Batch inserts in groups of 500 to amortise transaction overhead
