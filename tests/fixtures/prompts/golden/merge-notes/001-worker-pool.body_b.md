## Summary
Worker-pool tuning for the ingestion pipeline. Apply backpressure so a slow
writer does not overwhelm the queue and exhaust memory under load.

## Key Learnings
- Use a single writer thread to serialise writes and preserve ordering
- Add a bounded queue with backpressure signalling to the producers
- Drop pending work once the queue exceeds 10k items
