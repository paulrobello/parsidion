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

from tests.fake_parsight import FakeHealth, FakeMcpDaemon, FakeParsight  # noqa: E402


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """Return a fresh temporary vault root and wire resolve_vault() to it.

    Sets the ``CLAUDE_VAULT`` environment variable to ``tmp_path`` so that
    ``resolve_vault()`` returns ``tmp_path`` for the duration of the test,
    then clears the ``resolve_vault`` and ``load_config`` LRU caches before
    and after the test.

    SEC-P001: The resolver is now allowlist-based -- CLAUDE_VAULT references
    are accepted only when the path is registered in ``vaults.yaml``.  This
    fixture writes a test-local ``vaults.yaml`` (via ``XDG_CONFIG_HOME``)
    registering ``tmp_path`` so the allowlist check passes.

    The fixture does NOT create vault subdirectories — tests that need the
    standard layout should call ``vault_common.ensure_vault_dirs(tmp_vault)``
    or create dirs manually.
    """
    # Clear caches before setting the env var so any residual cached entry
    # from a previous test cannot bleed into this one.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.clear_config_cache()

    # SEC-P001: register tmp_path in a test-local vaults.yaml so the
    # allowlist resolver accepts CLAUDE_VAULT references to it.
    _cfg_dir = tmp_path / ".config" / "parsidion"
    _cfg_dir.mkdir(parents=True, exist_ok=True)
    (_cfg_dir / "vaults.yaml").write_text(
        f"vaults:\n  test: {tmp_path}\n", encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_path))

    yield tmp_path

    # Teardown: clear caches so the next test starts clean.
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]
    vault_common.clear_config_cache()


@pytest.fixture(autouse=True)
def _parsight_isolation(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Keep every test away from a real parsight daemon.

    Pins PARSIGHT_MCP_URL at an unreachable loopback port (connection refused
    instantly) so `resolve_parsight_backend()` can only succeed when a test
    opts into the `fake_parsight_health` fixture, and clears the per-process
    availability cache around each test. The try/except guards against a
    future state where parsight_backend is removed or renamed.
    """
    monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:1/mcp")
    try:
        import parsight_backend

        parsight_backend.reset_parsight_cache()
    except ImportError:
        pass
    yield
    try:
        import parsight_backend

        parsight_backend.reset_parsight_cache()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _no_ambient_xdg_config_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop the runner's ``XDG_CONFIG_HOME`` so tests observe the default path.

    CI runners export ``XDG_CONFIG_HOME`` pointing at the real user config,
    which diverges from each test's monkeypatched ``HOME``. Since the
    installer resolves vaults.yaml through ``get_vaults_config_path`` (which
    honors XDG), an ambient value silently redirected those writes to the
    runner's config dir and broke every HOME-based vaults.yaml test. Tests
    that exercise XDG resolution set the variable themselves (or via the
    ``tmp_vault`` fixture), which overrides this deletion.
    """
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


@pytest.fixture()
def fake_parsight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeParsight:
    """Install a fake `parsight` executable at the front of PATH."""
    bin_dir = tmp_path / "fake-parsight-bin"
    bin_dir.mkdir()
    fake = FakeParsight(bin_dir)
    fake.install()
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return fake


@pytest.fixture()
def mcp_daemon(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeMcpDaemon]:
    """Serve /health plus a minimal MCP endpoint; point PARSIGHT_MCP_URL at it.

    Unlike ``fake_parsight_health`` (health only — every POST fails, so the
    watch-coverage probe degrades to "unknown"), this daemon answers the
    probe, letting tests pin both the skip path and the spawn-anyway path.
    """
    daemon = FakeMcpDaemon().start()
    monkeypatch.setenv("PARSIGHT_MCP_URL", daemon.url)
    yield daemon
    daemon.stop()


@pytest.fixture()
def fake_parsight_health(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeHealth]:
    """Serve 200 on /health from an ephemeral port; point PARSIGHT_MCP_URL at it."""
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
    monkeypatch.setenv("PARSIGHT_MCP_URL", handle.url)
    yield handle
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()
