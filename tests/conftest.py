"""Shared pytest fixtures for the Parsidion test suite.

ARC-009: Centralised vault-isolation fixture.

The ``tmp_vault`` fixture redirects ``resolve_vault()`` to a temporary
directory via the ``CLAUDE_VAULT`` environment variable — the same public
override path used by production callers.  This replaces the earlier pattern
of ``monkeypatch.setattr(vault_common, "VAULT_ROOT", tmp_path)`` which
relied on a ``sys.modules`` inspection branch inside
``_resolve_vault_cached`` (see vault_path.py for why that branch must stay
for runtime callers like ``update_index.py``).

Usage in a test module::

    def test_something(tmp_vault: Path) -> None:
        # tmp_vault is the resolved vault root (a fresh tmp_path)
        ...

Or as autouse in a test class::

    @pytest.fixture(autouse=True)
    def _use_vault(self, tmp_vault: Path) -> None:
        pass  # side-effect: CLAUDE_VAULT is set for all tests in the class
"""

from __future__ import annotations

import http.server
import os
import sys
import threading
from collections.abc import Generator
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import vault_common  # noqa: E402

from tests.fake_parmem import FakeHealth, FakeParMem  # noqa: E402


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Return a fresh temporary vault root and wire resolve_vault() to it.

    Sets the ``CLAUDE_VAULT`` environment variable to ``tmp_path`` so that
    ``resolve_vault()`` returns ``tmp_path`` for the duration of the test,
    then clears the ``resolve_vault`` and ``load_config`` LRU caches before
    and after the test.

    The fixture does NOT create vault subdirectories — tests that need the
    standard layout should call ``vault_common.ensure_vault_dirs(tmp_vault)``
    or create dirs manually.
    """
    # Clear caches before setting the env var so any residual cached entry
    # from a previous test cannot bleed into this one.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()

    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))

    yield tmp_path

    # Teardown: clear caches so the next test starts clean.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.load_config.cache_clear()


@pytest.fixture(autouse=True)
def _parmem_isolation(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Keep every test away from a real par-mem daemon.

    Pins PARMEM_MCP_URL at an unreachable loopback port (connection refused
    instantly) so `resolve_parmem_backend()` can only succeed when a test
    opts into the `fake_parmem_health` fixture, and clears the per-process
    availability cache around each test. The parmem_backend module does not
    exist until Task 2 lands — the ImportError guard covers Task 1.
    """
    monkeypatch.setenv("PARMEM_MCP_URL", "http://127.0.0.1:1/mcp")
    try:
        import parmem_backend  # type: ignore[reportMissingImports]

        parmem_backend.reset_parmem_cache()
    except ImportError:
        pass
    yield
    try:
        import parmem_backend  # type: ignore[reportMissingImports]

        parmem_backend.reset_parmem_cache()
    except ImportError:
        pass


@pytest.fixture()
def fake_parmem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeParMem:
    """Install a fake `par-mem` executable at the front of PATH."""
    bin_dir = tmp_path / "fake-parmem-bin"
    bin_dir.mkdir()
    fake = FakeParMem(bin_dir)
    fake.install()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return fake


@pytest.fixture()
def fake_parmem_health(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeHealth]:
    """Serve 200 on /health from an ephemeral port; point PARMEM_MCP_URL at it."""
    handle = FakeHealth(url="")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server API name
            handle.requests.append(self.path)
            if self.path == "/health":
                body = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """Silence per-request stderr logging."""

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handle.url = f"http://127.0.0.1:{server.server_port}/mcp"
    monkeypatch.setenv("PARMEM_MCP_URL", handle.url)
    yield handle
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()
