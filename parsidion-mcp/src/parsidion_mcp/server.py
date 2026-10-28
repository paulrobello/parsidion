"""parsidion-mcp: FastMCP server exposing Parsidion vault to Claude Desktop."""

import functools
import inspect
import time

from fastmcp import FastMCP

from parsidion_mcp.metrics import init_metrics, record_tool_call
from parsidion_mcp.tools.code_search import code_search
from parsidion_mcp.tools.context import vault_context
from parsidion_mcp.tools.notes import vault_read, vault_write
from parsidion_mcp.tools.ops import rebuild_index, vault_doctor, vault_health
from parsidion_mcp.tools.search import vault_search

mcp = FastMCP("parsidion-mcp")


def _instrumented(fn):
    """Wrap a tool so each call records a parsidion_mcp_tool_calls datapoint.

    Preserves sync/async shape (FastMCP inspects it) and the original
    signature via functools.wraps; errors count as outcome="error" and are
    re-raised.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception:  # noqa: BLE001 — instrumentation boundary; re-raises
                record_tool_call(fn.__name__, "error", time.perf_counter() - started)
                raise
            record_tool_call(fn.__name__, "ok", time.perf_counter() - started)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 — instrumentation boundary; re-raises
            record_tool_call(fn.__name__, "error", time.perf_counter() - started)
            raise
        record_tool_call(fn.__name__, "ok", time.perf_counter() - started)
        return result

    return wrapper


mcp.tool()(_instrumented(vault_search))
mcp.tool()(_instrumented(vault_read))
mcp.tool()(_instrumented(vault_write))
mcp.tool()(_instrumented(vault_context))
mcp.tool()(_instrumented(rebuild_index))
mcp.tool()(_instrumented(vault_doctor))
mcp.tool()(_instrumented(vault_health))
mcp.tool()(_instrumented(code_search))


def main() -> None:
    """Entry point for the ``parsidion-mcp`` command."""
    init_metrics()
    mcp.run()
