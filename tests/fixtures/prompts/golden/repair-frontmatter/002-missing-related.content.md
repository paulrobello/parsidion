---
date: 2026-07-31
type: pattern
tags: [worker-pool, backoff]
confidence: high
sources: []
---
# Worker pool backoff

Use exponential backoff with jitter in the worker pool to avoid thundering
herds when retrying failed jobs. A capped backoff keeps the queue drained
without overwhelming downstream services.
