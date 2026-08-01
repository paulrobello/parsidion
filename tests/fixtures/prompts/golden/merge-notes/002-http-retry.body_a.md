## Summary
HTTP retry strategy for the API client. Use exponential backoff to avoid
hammering a degraded upstream service and to respect Retry-After headers when
they are present.

## Key Learnings
- Retry on 429 and 503 responses
- Double the delay each attempt starting at 100ms
- Cap the delay at 5 seconds per retry
