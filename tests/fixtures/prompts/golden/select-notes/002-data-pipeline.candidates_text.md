### ETL retry logic (Patterns/data-pipeline-retries.md)
Exponential-backoff wrapper around the data-pipeline step runner. Retries
transient S3 and database errors up to 5 times before failing the batch, then
emits a structured failure event to the dead-letter queue. Apply this pattern
when adding new pipeline stages that call flaky external services.

### Guide to knitting (Knowledge/knitting.md)
Yarn-weight chart and basic stitch patterns for hand knitting. A crafting
hobby note with no software engineering content.

### Python asyncio patterns (Patterns/asyncio-patterns.md)
General reference for asyncio.gather, task groups, and cancellation. Broadly
useful for async Python work but not specific to data-pipeline.

### SQL window functions (Knowledge/sql-window-functions.md)
Reference for OVER, PARTITION BY, and ROW_NUMBER. Generic SQL knowledge
applicable to any analytical query work.
