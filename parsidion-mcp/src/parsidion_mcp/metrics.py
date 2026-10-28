"""Env-gated OTLP metrics for parsidion-mcp (fleet OTel enablement).

Off by default: with no OTEL_EXPORTER_OTLP_METRICS_ENDPOINT (or the generic
OTEL_EXPORTER_OTLP_ENDPOINT) nothing is registered and every record call is
an attribute check. When the endpoint env is set, init_metrics() lazily
imports the OpenTelemetry SDK, builds a module-local MeterProvider with a
PeriodicExportingMetricReader on the OTLP/HTTP exporter (extra headers via
OTEL_EXPORTER_OTLP_HEADERS, standard comma-separated key=value pairs with
URL-encoded values), and creates the instruments below.

Temporality is the SDK default CUMULATIVE: Prometheus's OTLP ingest
(par-obs's collector) silently DROPS delta datapoints, and its rate()/
increase() are counter-reset aware, so restarts are safe.

Series (parsidion_mcp_ prefix, matching the par_artifacts_/par_mesh_ fleet
convention):
    parsidion_mcp_tool_calls            {tool, outcome}  completed tool calls
    parsidion_mcp_tool_duration_seconds {tool}           tool latency
    parsidion_mcp_index_age_seconds     gauge            vault index age
    parsidion_mcp_up                    gauge            process presence
"""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from typing import Any

from urllib.parse import unquote

METER_NAME = "parsidion-mcp"
SERVICE_NAME = "parsidion-mcp"
DEFAULT_EXPORT_INTERVAL_MS = 60_000


def otlp_metrics_url(env: Any = None) -> str | None:
    """Resolve the OTLP/HTTP metrics receiver URL from standard OTEL env vars.

    The METRICS-specific endpoint wins; the generic endpoint gets the
    standard /v1/metrics signal path appended (trailing slashes stripped).
    """
    environ = os.environ if env is None else env
    specific = environ.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "").strip()
    if specific:
        return specific
    generic = environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if generic:
        return generic.rstrip("/") + "/v1/metrics"
    return None


def is_otlp_enabled(env: Any = None) -> bool:
    environ = os.environ if env is None else env
    return otlp_metrics_url(environ) is not None


def parse_otlp_headers(env: Any = None) -> dict[str, str]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS (METRICS variant wins) into a dict."""
    environ = os.environ if env is None else env
    headers: dict[str, str] = {}
    for raw in (
        environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""),
        environ.get("OTEL_EXPORTER_OTLP_METRICS_HEADERS", ""),
    ):
        for pair in raw.split(","):
            eq = pair.find("=")
            if eq <= 0:
                continue
            key = pair[:eq].strip()
            value = pair[eq + 1 :].strip()
            if key and value:
                headers[key] = unquote(value)
    return headers


def _export_interval_ms(env: Any) -> int:
    raw = env.get("OTEL_METRIC_EXPORT_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_EXPORT_INTERVAL_MS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_EXPORT_INTERVAL_MS
    return value if value > 0 else DEFAULT_EXPORT_INTERVAL_MS


@dataclass
class _Instruments:
    calls: Any
    duration: Any
    up: Any
    index_age: Any


_provider: Any = None
_instruments: _Instruments | None = None


def _observe_up(_options: Any) -> list[Any]:
    from opentelemetry.metrics import Observation

    return [Observation(1.0)]


def _observe_index_age(_options: Any) -> list[Any]:
    from opentelemetry.metrics import Observation

    import vault_common

    db = vault_common.resolve_vault(explicit=None) / "embeddings.db"
    try:
        mtime = db.stat().st_mtime
    except OSError:
        return []
    return [Observation(max(0.0, time.time() - mtime))]


def _build_instruments(meter: Any) -> _Instruments:
    return _Instruments(
        calls=meter.create_counter(
            "parsidion_mcp_tool_calls",
            description="Completed MCP tool calls by tool and outcome",
        ),
        duration=meter.create_histogram(
            "parsidion_mcp_tool_duration_seconds",
            description="MCP tool call latency by tool",
            unit="s",
        ),
        up=meter.create_observable_gauge(
            "parsidion_mcp_up",
            callbacks=[_observe_up],
            description="Process presence (1 while the server runs)",
        ),
        index_age=meter.create_observable_gauge(
            "parsidion_mcp_index_age_seconds",
            callbacks=[_observe_index_age],
            description="Age of the vault index (embeddings.db mtime)",
        ),
    )


def init_metrics(env: Any = None, *, reader: Any = None) -> bool:
    """Register the module-local MeterProvider; True only when enabled.

    Idempotent once enabled (repeat calls return True without rebuilding);
    a disabled run (no endpoint env) is NOT cached, so a later enabled call
    still registers. The optional ``reader`` parameter exists for tests
    (InMemoryMetricReader); production passes None and gets a
    PeriodicExportingMetricReader on the OTLP/HTTP exporter.
    """
    global _provider, _instruments
    if _provider is not None:
        return True
    environ = os.environ if env is None else env
    url = otlp_metrics_url(environ)
    if not url:
        return False
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource

    # Cumulative (SDK default): Prometheus's OTLP ingest silently drops
    # delta datapoints, and its rate()/increase() are counter-reset aware.
    exporter = OTLPMetricExporter(
        endpoint=url,
        headers=parse_otlp_headers(environ),
    )
    reader_obj = (
        reader
        if reader is not None
        else PeriodicExportingMetricReader(
            exporter, export_interval_millis=_export_interval_ms(environ)
        )
    )
    provider = MeterProvider(
        metric_readers=[reader_obj],
        resource=Resource.create(
            {
                "service.name": SERVICE_NAME,
                "service.instance.id": socket.gethostname(),
            }
        ),
    )
    _provider = provider
    _instruments = _build_instruments(provider.get_meter(METER_NAME))
    return True


def record_tool_call(tool: str, outcome: str, duration_s: float) -> None:
    """Record one completed tool call (no-op unless metrics are initialized)."""
    inst = _instruments
    if inst is None:
        return
    inst.calls.add(1, {"tool": tool, "outcome": outcome})
    inst.duration.record(duration_s, {"tool": tool})


def shutdown_metrics() -> None:
    """Flush and release the provider; records become no-ops again (tests)."""
    global _provider, _instruments
    provider = _provider
    _provider = None
    _instruments = None
    if provider is not None:
        try:
            provider.shutdown()
        except Exception:  # noqa: BLE001 — a failed final flush must not crash shutdown
            pass
