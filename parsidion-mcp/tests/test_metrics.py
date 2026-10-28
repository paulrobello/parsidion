"""Tests for env-gated OTLP metrics (parsidion_mcp.metrics)."""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import vault_path

from parsidion_mcp import metrics

OTEL_ENV_KEYS = [
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    "OTEL_METRIC_EXPORT_INTERVAL",
]


@pytest.fixture()
def env_no_otlp(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Hermetic env: no OTEL_* vars, vault pinned to an empty tmp dir."""
    for key in OTEL_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    metrics.shutdown_metrics()
    yield
    metrics.shutdown_metrics()


def _collect_names(reader: Any) -> dict[str, Any]:
    names: dict[str, Any] = {}
    data = reader.get_metrics_data()
    if data is None:
        return names
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                names[m.name] = m.data
    return names


def _attr(dp: Any) -> dict[str, str]:
    return {k: v for k, v in dict(dp.attributes).items()}


def test_otlp_metrics_url_specific_wins() -> None:
    env: dict[str, str] = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4318",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://b:4318/custom/metrics",
    }
    assert metrics.otlp_metrics_url(env) == "http://b:4318/custom/metrics"


def test_otlp_metrics_url_generic_appends_path() -> None:
    env: dict[str, str] = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4318/"}
    assert metrics.otlp_metrics_url(env) == "http://a:4318/v1/metrics"


def test_otlp_metrics_url_unset_is_none() -> None:
    env: dict[str, str] = {}
    assert metrics.otlp_metrics_url(env) is None
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "   "
    assert metrics.otlp_metrics_url(env) is None


def test_is_otlp_enabled() -> None:
    assert metrics.is_otlp_enabled({}) is False
    assert (
        metrics.is_otlp_enabled({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://a:4318"})
        is True
    )


def test_parse_otlp_headers() -> None:
    env: dict[str, str] = {
        "OTEL_EXPORTER_OTLP_HEADERS": "api-key=%20abc,ok=1,broken",
    }
    assert metrics.parse_otlp_headers(env) == {"api-key": " abc", "ok": "1"}


def test_parse_otlp_headers_metrics_variant_wins() -> None:
    env: dict[str, str] = {
        "OTEL_EXPORTER_OTLP_HEADERS": "a=1",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS": "a=2",
    }
    assert metrics.parse_otlp_headers(env) == {"a": "2"}


def test_init_metrics_disabled_returns_false(env_no_otlp: None) -> None:
    assert metrics.init_metrics() is False


def test_record_before_init_is_noop(env_no_otlp: None) -> None:
    metrics.record_tool_call("vault_search", "ok", 0.01)
    assert metrics.is_otlp_enabled() is False


def test_init_metrics_enabled_and_records(
    env_no_otlp: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    db = tmp_path / "embeddings.db"
    db.write_bytes(b"stub")
    old = time.time() - 900.0
    os.utime(db, (old, old))
    reader = InMemoryMetricReader()
    assert metrics.init_metrics(reader=reader) is True
    metrics.record_tool_call("vault_search", "ok", 0.05)
    with vault_path.active_vault_scope(tmp_path):
        names = _collect_names(reader)
    assert "parsidion_mcp_tool_calls" in names
    (dp,) = names["parsidion_mcp_tool_calls"].data_points
    assert _attr(dp) == {"tool": "vault_search", "outcome": "ok"}
    assert dp.value >= 1
    assert "parsidion_mcp_tool_duration_seconds" in names
    assert "parsidion_mcp_up" in names
    up_dp = names["parsidion_mcp_up"].data_points[0]
    assert up_dp.value == 1
    assert "parsidion_mcp_index_age_seconds" in names
    age_dp = names["parsidion_mcp_index_age_seconds"].data_points[0]
    assert 800 <= age_dp.value <= 1000
    # Idempotent: a second enabled init must not duplicate instruments.
    assert metrics.init_metrics(reader=InMemoryMetricReader()) is True


def test_init_metrics_index_age_absent_when_no_db(
    env_no_otlp: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
    reader = InMemoryMetricReader()
    assert metrics.init_metrics(reader=reader) is True
    with vault_path.active_vault_scope(tmp_path):
        names = _collect_names(reader)
    assert "parsidion_mcp_up" in names
    assert "parsidion_mcp_index_age_seconds" not in names


def test_shutdown_resets_state() -> None:
    # Disabled init is not cached, so records stay no-ops before and after.
    assert metrics.init_metrics({}) is False
    metrics.record_tool_call("t", "ok", 0.0)
    metrics.shutdown_metrics()
    metrics.record_tool_call("t", "ok", 0.0)
