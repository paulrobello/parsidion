# Observability

Parsidion ships telemetry through the fleet's par-obs LGTM stack (Prometheus
on lenny2). Two instrumentation surfaces exist, with different mechanics:

## parsidion-mcp (long-running server)

The MCP server instruments its tools with the OpenTelemetry SDK and exports
OTLP/HTTP when `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` (or the generic
`OTEL_EXPORTER_OTLP_ENDPOINT`) is set; no endpoint means nothing registers
and record calls are no-ops. Cumulative counter/histogram temporality —
Prometheus's OTLP ingest silently drops delta datapoints. Series carry the
`parsidion_mcp_` prefix (see `parsidion-mcp/README.md` for the full list).

## Hook layer and vault CLIs (short-lived processes)

Session hooks (via `write_hook_event`) and the `vault-*` CLIs plus
`update_index.py` push one **gauge datapoint of 1 per event** over raw
OTLP/HTTP JSON (stdlib `urllib` — the ARC-004 stdlib gate forbids the
OpenTelemetry SDK in `scripts/core/`). Series (gauge semantics):

| Series | Labels | Meaning |
|---|---|---|
| `parsidion_hook_events` | `hook`, `project` | one datapoint per hook execution |
| `parsidion_cli_invocations` | `tool` | one datapoint per CLI invocation |

**Why gauges, not counters:** hook/CLI processes are one-shot, so a
per-process counter would export `1` every time; Prometheus reads a
`1,1,1,...` series as zero increase, and delta temporality is dropped by
Prometheus's OTLP ingest. Panels must therefore count datapoints, not sum
counter increments.

### Dashboard expectation

Usage panels feed from these gauge series with `count_over_time`, e.g.:

```promql
sum by (hook) (count_over_time(parsidion_hook_events[10m]))
sum by (tool) (count_over_time(parsidion_cli_invocations[10m]))
```

(see also the `par_obs_up{service="parsidion"}` liveness tile — that prober
covers index freshness, not usage). No `_total` counter series exist from
this surface; a `_total` query returning empty is expected, not a bug.

### Configuration

Telemetry is off by default and requires **both** keys; otherwise emits are
no-ops:

```yaml
otlp:
  enabled: true
  endpoint: https://obs-ingest.example.net/v1/metrics
  headers: "Authorization=Bearer%20...,..."
  timeout_s: 2.0
  wait_s: 0.5
  service_name: parsidion
```

- `headers` uses `OTEL_EXPORTER_OTLP_HEADERS` syntax (comma-separated
  `key=value`, URL-encoded values). Auth headers (collector Bearer token,
  Cloudflare Access service token) belong in `config.local.yaml`, never in
  the git-synced `config.yaml`.
- Pushes run on a daemon thread; the process waits at most `wait_s`, then
  proceeds (the datapoint is dropped). A wedged network costs a hook at
  most `wait_s`, never a hang. Every failure is swallowed.
- **The exporter sends a named `User-Agent` (`parsidion-otlp/0.1`)**:
  Cloudflare's bot protection rejects the default python-urllib signature
  with error 1010.

Series land in the par-obs Prometheus (lenny2) through
`obs-ingest.pardev.net` (Cloudflare Access service token + collector Bearer
token; secrets live in parvault, not in this repo).
