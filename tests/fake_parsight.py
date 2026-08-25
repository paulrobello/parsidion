"""Fake `parsight` executable + fake daemon-health server for tests.

No parsidion test may require a real parsight install. The fake binary is a
generated Python script placed at the front of PATH; it reads its behavior
from a sibling ``config.json``, records every invocation (argv + cwd) to a
sibling ``calls.jsonl``, and emits canned JSON / exit codes / delays. The
fake health server answers 200 on ``/health`` so
``parsight_backend.resolve_parsight_backend()`` can be exercised end-to-end.
"""

from __future__ import annotations

import http.server
import json
import os
import sys
import threading
import time
from pathlib import Path


def fresh_repos_payload(vault: Path, *, stale: bool = False) -> dict:
    """Build a ``parsight repos --json`` payload classifying *vault*.

    ``parsight_backend._vault_repo_state`` matches by ``os.path.realpath`` on
    ``root_path`` or a worktree ``path`` and reads the worktree's ``stale``
    flag. By default the fake's ``repos`` payload is empty → ``"absent"``; this
    builds a payload the classifier reads as ``"fresh"`` (or ``"stale"`` with
    ``stale=True``) for the given vault, so enrichment-attempting tests clear
    the freshness gate in ``build_parsight_body_edges``.
    """
    rv = os.path.realpath(str(vault))
    return {
        "repositories": [
            {
                "root_path": rv,
                "worktrees": [{"path": rv, "is_primary": True, "stale": bool(stale)}],
            }
        ],
        "_meta": {"count": 1},
    }


# Script body for the fake binary. The shebang is prepended at install time
# with the CURRENT interpreter (sys.executable) so the script never depends
# on PATH containing python3 (tests routinely truncate PATH).
FAKE_PARSIGHT_BODY = """
import json, os, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
cfg = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
with open(HERE / "calls.jsonl", "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
if cfg.get("delay"):
    time.sleep(float(cfg["delay"]))
sub = sys.argv[1] if len(sys.argv) > 1 else ""
override = cfg.get("stdout_override")
if override is not None:
    sys.stdout.write(override)
elif sub == "find-code":
    sys.stdout.write(json.dumps(cfg.get("find_code", {"results": []})))
elif sub == "repos":
    sys.stdout.write(
        json.dumps(cfg.get("repos", {"repositories": [], "_meta": {"count": 0}}))
    )
elif sub == "doc-links":
    sys.stdout.write(
        json.dumps(cfg.get("doc_links", {"links": [], "total": 0, "truncated": False}))
    )
# index / watch / unwatch need no stdout for the backend's purposes.
stderr_output = cfg.get("stderr_output")
if stderr_output:
    sys.stderr.write(stderr_output)
exit_codes = cfg.get("exit_codes") or {}
sys.exit(int(exit_codes.get(sub, cfg.get("exit_code", 0))))
""".lstrip()


class FakeParsight:
    """Controller for a fake `parsight` executable installed on PATH.

    Reconfigure behavior between calls with :meth:`configure`; read recorded
    invocations with :meth:`calls` / :meth:`wait_for_call`.
    """

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.binary_path = bin_dir / "parsight"
        self.config_path = bin_dir / "config.json"
        self.calls_path = bin_dir / "calls.jsonl"

    def install(self) -> None:
        """Write the executable script (shebang = current interpreter)."""
        self.binary_path.write_text(
            f"#!{sys.executable}\n{FAKE_PARSIGHT_BODY}", encoding="utf-8"
        )
        self.binary_path.chmod(0o755)
        self.configure()

    def configure(
        self,
        *,
        find_code: object = None,
        repos: object = None,
        doc_links: object = None,
        exit_code: int = 0,
        exit_codes: dict[str, int] | None = None,
        delay: float = 0.0,
        stdout_override: str | None = None,
        stderr_output: str = "",
    ) -> None:
        """(Re)write the fake's behavior config.

        Args:
            find_code: JSON payload printed for `find-code`
                (default {"results": []} — the MCP find_code shape).
            repos: JSON payload printed for `repos` (default
                {"repositories": [], "_meta": {"count": 0}} — the MCP
                list_indexed_repositories shape).
            doc_links: JSON payload printed for `doc-links` (default
                {"links": [], "total": 0, "truncated": False} — the MCP
                doc-links shape).
            exit_code: exit code for every subcommand without an override.
            exit_codes: per-subcommand exit-code overrides, e.g. {"find-code": 1}.
            delay: seconds to sleep before responding (timeout tests; keep <= 3).
            stdout_override: printed verbatim for every subcommand (garbage-JSON tests).
            stderr_output: printed to stderr for every subcommand (failure-detail tests).
        """
        self.config_path.write_text(
            json.dumps(
                {
                    "find_code": find_code
                    if find_code is not None
                    else {"results": []},
                    "repos": repos
                    if repos is not None
                    else {"repositories": [], "_meta": {"count": 0}},
                    "doc_links": doc_links
                    if doc_links is not None
                    else {"links": [], "total": 0, "truncated": False},
                    "exit_code": exit_code,
                    "exit_codes": exit_codes or {},
                    "delay": delay,
                    "stdout_override": stdout_override,
                    "stderr_output": stderr_output,
                }
            ),
            encoding="utf-8",
        )

    def calls(self) -> list[dict[str, object]]:
        """Return recorded invocations: [{"argv": [...], "cwd": "..."}]."""
        if not self.calls_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.calls_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def wait_for_call(self, subcommand: str, timeout: float = 5.0) -> dict[str, object]:
        """Poll until an invocation whose first argv entry is *subcommand* appears."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for call in self.calls():
                argv = call.get("argv")
                if isinstance(argv, list) and argv and argv[0] == subcommand:
                    return call
            time.sleep(0.05)
        raise AssertionError(
            f"no fake parsight call with subcommand {subcommand!r}; got {self.calls()!r}"
        )

    def assert_no_call(self, subcommand: str, settle: float = 0.3) -> None:
        """Assert *subcommand* was never invoked (waits *settle* s for stragglers)."""
        time.sleep(settle)
        for call in self.calls():
            argv = call.get("argv")
            if isinstance(argv, list) and argv and argv[0] == subcommand:
                raise AssertionError(f"unexpected fake parsight call: {call!r}")


class FakeHealth:
    """Handle for the fake daemon health server (see conftest fixture)."""

    def __init__(self, url: str) -> None:
        self.url = url  # a .../mcp URL, mirroring PARSIGHT_MCP_URL's shape
        self.requests: list[str] = []


class FakeMcpDaemon:
    """Health + minimal MCP-over-HTTP daemon impersonator for tests.

    Serves ``GET /health`` (200) plus just enough of the streamable-HTTP MCP
    endpoint (``POST /mcp``) to exercise ``parsight_backend``'s one-shot
    watch-coverage probe: ``initialize`` answers with an ``mcp-session-id``
    header, and a session-less ``tools/call`` is rejected with 422 — both
    mirroring the real daemon, so a client that skips the handshake fails
    here like it would in production. ``tools/call list_watched_paths``
    returns ``self.watched_paths`` as an SSE ``data:`` body (the real
    daemon's response shape). ``raw_tools_response`` overrides the verbatim
    SSE body for error/garbage-shape tests (its JSON-RPC ``id`` must be 2,
    the id the backend's probe uses for ``tools/call``).

    The parsight CLI has no watch-list subcommand, so this HTTP surface is
    the only way to fake "is the vault watched by the daemon".
    """

    def __init__(self) -> None:
        self.watched_paths: list[str] = []
        self.serve_mcp: bool = True
        self.raw_tools_response: str | None = None
        self.mcp_calls: list[dict[str, object]] = []
        self.url = ""
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> FakeMcpDaemon:
        """Bind an ephemeral loopback port and serve until :meth:`stop`."""
        ctl = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — http.server API name
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

            def do_POST(self) -> None:  # noqa: N802 — http.server API name
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if self.path.rstrip("/") != "/mcp" or not ctl.serve_mcp:
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    message = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    self.send_response(400)
                    self.end_headers()
                    return
                ctl.mcp_calls.append(
                    {
                        "method": message.get("method")
                        if isinstance(message, dict)
                        else None,
                        "session": self.headers.get("Mcp-Session-Id"),
                    }
                )
                if not isinstance(message, dict):
                    self.send_response(400)
                    self.end_headers()
                    return
                if message.get("method") == "initialize":
                    self._send_sse(
                        {
                            "jsonrpc": "2.0",
                            "id": message.get("id"),
                            "result": {
                                "protocolVersion": "2025-06-18",
                                "capabilities": {"tools": {}},
                                "serverInfo": {
                                    "name": "fake-parsight",
                                    "version": "0",
                                },
                            },
                        },
                        extra_headers=[
                            ("mcp-session-id", f"fake-{len(ctl.mcp_calls)}")
                        ],
                    )
                    return
                if message.get("method") == "tools/call":
                    if not self.headers.get("Mcp-Session-Id"):
                        # Real daemon: "Unexpected message, expect initialize
                        # request" — a session-less call must not succeed.
                        self.send_response(422)
                        self.end_headers()
                        return
                    if ctl.raw_tools_response is not None:
                        self._send_raw_sse(ctl.raw_tools_response)
                        return
                    name = (message.get("params") or {}).get("name")
                    if name == "list_watched_paths":
                        text = json.dumps(
                            {"watched_paths": ctl.watched_paths, "watches": []}
                        )
                        self._send_sse(
                            {
                                "jsonrpc": "2.0",
                                "id": message.get("id"),
                                "result": {
                                    "content": [{"type": "text", "text": text}],
                                    "isError": False,
                                },
                            }
                        )
                    else:
                        self._send_sse(
                            {
                                "jsonrpc": "2.0",
                                "id": message.get("id"),
                                "error": {
                                    "code": -32601,
                                    "message": f"unknown tool {name!r}",
                                },
                            }
                        )
                    return
                # JSON-RPC notifications (no id) are accepted with 202.
                self.send_response(202)
                self.end_headers()

            def _send_sse(
                self,
                payload: dict[str, object],
                extra_headers: list[tuple[str, str]] | None = None,
            ) -> None:
                self._send_raw_sse(
                    f"data: {json.dumps(payload)}\n\n", extra_headers or []
                )

            def _send_raw_sse(
                self,
                body: str,
                extra_headers: list[tuple[str, str]] | None = None,
            ) -> None:
                data = body.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                for key, value in extra_headers or []:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                """Silence per-request stderr logging."""

        self._server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = f"http://127.0.0.1:{self._server.server_port}/mcp"
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._server.server_close()
            self._server = None
            self._thread = None
