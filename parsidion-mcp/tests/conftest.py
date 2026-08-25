"""parsidion-mcp test isolation: never touch a real parsight daemon."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _parsight_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:1/mcp")
    try:
        import parsight_backend

        parsight_backend.reset_parsight_cache()
    except ImportError:
        pass
