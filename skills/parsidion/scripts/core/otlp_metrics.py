"""Env-free, config-gated OTLP usage telemetry for the hook layer and CLIs.

parsidion's real usage flows through short-lived processes (session hooks
and the ``vault-*`` CLIs), which cannot keep OTLP counter state across
invocations: a per-process counter always exports ``1``, and Prometheus
reads a 1,1,1,... series as zero increase. This module therefore exports
one **gauge datapoint of 1 per event** (``parsidion_hook_events`` /
``parsidion_cli_invocations``) and dashboards count events with
``count_over_time`` — the standard pattern for serverless-style emitters.

Transport is raw OTLP/HTTP JSON (stdlib ``urllib`` — the ARC-004 stdlib
gate forbids the OpenTelemetry SDK here). Off by default: nothing is sent
unless the vault config enables it AND sets an endpoint:

.. code-block:: yaml

    otlp:
      enabled: true
      endpoint: https://obs-ingest.example.net/v1/metrics
      headers: "Authorization=Bearer%20...%3D,CF-Access-Client-Id=..."   # secrets -> config.local.yaml

Pushes run on a daemon thread bounded by ``wait_s``: on a healthy network
the POST completes and is delivered before the hook process exits; on a
wedged network the wait lapses, the hook proceeds, and that datapoint is
dropped. Telemetry is best-effort by design and must never delay or break
a hook (every failure path is swallowed).
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from .vault_config import get_config

__all__ = [
    "OtlpSettings",
    "SERVICE_NAME_DEFAULT",
    "build_usage_payload",
    "emit_usage_metric",
    "load_otlp_settings",
    "parse_otlp_headers",
    "post_metrics",
]

SERVICE_NAME_DEFAULT = "parsidion"
DEFAULT_TIMEOUT_S = 2.0
DEFAULT_WAIT_S = 0.5
SCOPE_NAME = "parsidion-vault"
USER_AGENT = "parsidion-otlp/0.1"


@dataclass
class OtlpSettings:
    """Resolved ``otlp`` config section (endpoint must be non-empty)."""

    endpoint: str
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = DEFAULT_TIMEOUT_S
    wait_s: float = DEFAULT_WAIT_S
    service_name: str = SERVICE_NAME_DEFAULT


def parse_otlp_headers(raw: str) -> dict[str, str]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS syntax (``k=v,k2=v2``, URL-quoted
    values) into a dict; malformed pairs are skipped."""
    headers: dict[str, str] = {}
    for pair in (raw or "").split(","):
        eq = pair.find("=")
        if eq <= 0:
            continue
        key = pair[:eq].strip()
        value = pair[eq + 1 :].strip()
        if key and value:
            headers[key] = unquote(value)
    return headers


def load_otlp_settings() -> OtlpSettings | None:
    """Read the ``otlp`` config section; None unless enabled AND endpoint set."""
    if not get_config("otlp", "enabled", False):
        return None
    endpoint = str(get_config("otlp", "endpoint", "") or "").strip()
    if not endpoint:
        return None
    timeout_s = float(
        get_config("otlp", "timeout_s", DEFAULT_TIMEOUT_S) or DEFAULT_TIMEOUT_S
    )
    wait_s = float(get_config("otlp", "wait_s", DEFAULT_WAIT_S) or DEFAULT_WAIT_S)
    service_name = (
        str(
            get_config("otlp", "service_name", SERVICE_NAME_DEFAULT)
            or SERVICE_NAME_DEFAULT
        ).strip()
        or SERVICE_NAME_DEFAULT
    )
    headers_raw = str(get_config("otlp", "headers", "") or "")
    return OtlpSettings(
        endpoint=endpoint,
        headers=parse_otlp_headers(headers_raw),
        timeout_s=max(0.1, timeout_s),
        wait_s=max(0.0, wait_s),
        service_name=service_name,
    )


def build_usage_payload(
    metric_name: str,
    value: float,
    attrs: dict[str, str],
    service_name: str,
) -> dict[str, Any]:
    """Build an OTLP/JSON ``ExportMetricsServiceRequest`` (camelCase proto-JSON
    field names) carrying one gauge datapoint."""
    now_ns = str(time.time_ns())
    attributes = [
        {"key": key, "value": {"stringValue": str(val)}}
        for key, val in sorted(attrs.items())
    ]
    datapoint: dict[str, Any] = {
        "timeUnixNano": now_ns,
        "asDouble": float(value),
    }
    if attributes:
        datapoint["attributes"] = attributes
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}},
                        {
                            "key": "host.name",
                            "value": {"stringValue": socket.gethostname()},
                        },
                    ]
                },
                "scopeMetrics": [
                    {
                        "scope": {"name": SCOPE_NAME},
                        "metrics": [
                            {
                                "name": metric_name,
                                "gauge": {"dataPoints": [datapoint]},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def post_metrics(
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout_s: float,
) -> int:
    """POST one OTLP/JSON payload; raises on network/HTTP failure."""
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # A named User-Agent is required in production: Cloudflare's bot
    # protection (obs-ingest sits behind an Access app) rejects the default
    # python-urllib signature with error 1010 (measured 2026-10-28).
    req.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
        resp.read()
    return getattr(resp, "status", 0)


def emit_usage_metric(
    metric_name: str,
    value: float = 1.0,
    attrs: dict[str, str] | None = None,
    *,
    settings: OtlpSettings | None = None,
) -> bool:
    """Fire-and-forget one usage datapoint; True only when delivered.

    Never raises. The POST runs on a daemon thread; the caller waits at
    most ``wait_s`` so a healthy network delivers in-process while a wedged
    one only costs the wait, never a hang. ``settings`` exists for tests.
    """
    try:
        resolved = settings if settings is not None else load_otlp_settings()
        if resolved is None:
            return False
        body = json.dumps(
            build_usage_payload(metric_name, value, attrs or {}, resolved.service_name)
        ).encode("utf-8")
        delivered = threading.Event()

        def _push() -> None:
            try:
                post_metrics(
                    resolved.endpoint, body, resolved.headers, resolved.timeout_s
                )
                delivered.set()
            except Exception:  # noqa: BLE001 — telemetry must never propagate
                pass

        thread = threading.Thread(target=_push, daemon=True, name="parsidion-otlp")
        thread.start()
        delivered.wait(resolved.wait_s)
        return delivered.is_set()
    except Exception:  # noqa: BLE001 — telemetry must never propagate
        return False
