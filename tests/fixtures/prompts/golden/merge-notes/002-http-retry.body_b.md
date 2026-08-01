## Summary
HTTP retry strategy for the API client. Add jitter to the backoff schedule so a
thundering herd of retries does not synchronise across independent clients.

## Key Learnings
- Retry on 429 and 5xx responses
- Add up to 20% jitter to each computed delay
- Give up after 5 attempts and surface the error to the caller
