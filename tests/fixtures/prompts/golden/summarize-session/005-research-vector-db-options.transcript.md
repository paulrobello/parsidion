# Session transcript — research: vector DB options for a side project

User: What vector DB should I use for ~10k notes with semantic search?

Assistant: For 10k items you don't need a dedicated vector DB at all. Options ranked by complexity:

1. sqlite-vec (recommended): an extension on your existing SQLite. Zero new infra, sub-ms queries at 10k scale, works with the embeddings.db pattern you already have.
2. LanceDB: embedded columnar store, good to ~1M vectors, slightly more setup.
3. Qdrant/Weaviate: dedicated servers. Overkill at 10k, justified around 1M+ or when you need filtering/multi-tenant.

The breaking point for sqlite-vec is around 100k-500k vectors where a full scan starts to exceed your latency budget — at that point you want HNSW indexing, which sqlite-vec added experimentally but a dedicated DB does better.

User: Cost?

Assistant: sqlite-vec and LanceDB are free/self-hosted. Qdrant Cloud starts around $25/mo for 1GB. At 10k notes * 384 dims * 4 bytes = ~15MB, you're nowhere near any paid tier.

Recommendation: stick with sqlite-vec. Migrate only when you have a measured latency problem, not preemptively.
