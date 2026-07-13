"""parsidion-mcp test isolation: never touch a real par-mem daemon."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _parmem_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARMEM_MCP_URL", "http://127.0.0.1:1/mcp")
    try:
        import parmem_backend

        parmem_backend.reset_parmem_cache()
    except ImportError:
        pass
