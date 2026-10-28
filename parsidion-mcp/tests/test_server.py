"""Smoke tests for server.py wiring."""

import asyncio
import time
from typing import Any

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from parsidion_mcp import metrics
from parsidion_mcp.server import _instrumented, mcp


@pytest.fixture()
def metrics_reader() -> Any:
    reader = InMemoryMetricReader()
    metrics.init_metrics(
        {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318"}, reader=reader
    )
    yield reader
    metrics.shutdown_metrics()


def _find_dp(reader: Any, metric: str) -> Any:
    data = reader.get_metrics_data()
    if data is None:
        return None
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == metric:
                    return m.data.data_points[0]
    return None


def test_mcp_instance_exists() -> None:
    assert mcp is not None
    assert mcp.name == "parsidion-mcp"


def test_all_tool_modules_importable() -> None:
    """Verify all tool functions are importable and callable.

    Avoids FastMCP private internals (_tool_manager) which may change
    between versions. Correct registration is implicitly verified: if
    server.py imports cleanly and mcp.tool()(fn) raised no error at
    module load time, all tools are registered.
    """
    from parsidion_mcp.tools.code_search import code_search
    from parsidion_mcp.tools.context import vault_context
    from parsidion_mcp.tools.notes import vault_read, vault_write
    from parsidion_mcp.tools.ops import rebuild_index, vault_doctor
    from parsidion_mcp.tools.search import vault_search

    for fn in [
        vault_search,
        vault_read,
        vault_write,
        vault_context,
        rebuild_index,
        vault_doctor,
        code_search,
    ]:
        assert callable(fn)


def test_instrumented_sync_tool_records_ok(metrics_reader: Any) -> None:
    @_instrumented
    def my_tool() -> str:
        time.sleep(0.001)
        return "payload"

    assert my_tool() == "payload"
    dp = _find_dp(metrics_reader, "parsidion_mcp_tool_calls")
    assert dp is not None
    assert dict(dp.attributes) == {"tool": "my_tool", "outcome": "ok"}


def test_instrumented_async_tool_records_ok(metrics_reader: Any) -> None:
    @_instrumented
    async def my_async_tool() -> str:
        return "async-payload"

    assert asyncio.run(my_async_tool()) == "async-payload"
    dp = _find_dp(metrics_reader, "parsidion_mcp_tool_calls")
    assert dp is not None
    assert dict(dp.attributes) == {"tool": "my_async_tool", "outcome": "ok"}


def test_instrumented_tool_records_error_and_reraises(metrics_reader: Any) -> None:
    @_instrumented
    def boom() -> str:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        boom()
    dp = _find_dp(metrics_reader, "parsidion_mcp_tool_calls")
    assert dp is not None
    assert dict(dp.attributes) == {"tool": "boom", "outcome": "error"}
