"""Fake `par-mem` executable + fake daemon-health server for tests.

No parsidion test may require a real par-mem install. The fake binary is a
generated Python script placed at the front of PATH; it reads its behavior
from a sibling ``config.json``, records every invocation (argv + cwd) to a
sibling ``calls.jsonl``, and emits canned JSON / exit codes / delays. The
fake health server answers 200 on ``/health`` so
``parmem_backend.resolve_parmem_backend()`` can be exercised end-to-end.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Script body for the fake binary. The shebang is prepended at install time
# with the CURRENT interpreter (sys.executable) so the script never depends
# on PATH containing python3 (tests routinely truncate PATH).
FAKE_PARMEM_BODY = """
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


class FakeParMem:
    """Controller for a fake `par-mem` executable installed on PATH.

    Reconfigure behavior between calls with :meth:`configure`; read recorded
    invocations with :meth:`calls` / :meth:`wait_for_call`.
    """

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.binary_path = bin_dir / "par-mem"
        self.config_path = bin_dir / "config.json"
        self.calls_path = bin_dir / "calls.jsonl"

    def install(self) -> None:
        """Write the executable script (shebang = current interpreter)."""
        self.binary_path.write_text(
            f"#!{sys.executable}\n{FAKE_PARMEM_BODY}", encoding="utf-8"
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
            f"no fake par-mem call with subcommand {subcommand!r}; got {self.calls()!r}"
        )

    def assert_no_call(self, subcommand: str, settle: float = 0.3) -> None:
        """Assert *subcommand* was never invoked (waits *settle* s for stragglers)."""
        time.sleep(settle)
        for call in self.calls():
            argv = call.get("argv")
            if isinstance(argv, list) and argv and argv[0] == subcommand:
                raise AssertionError(f"unexpected fake par-mem call: {call!r}")


class FakeHealth:
    """Handle for the fake daemon health server (see conftest fixture)."""

    def __init__(self, url: str) -> None:
        self.url = url  # a .../mcp URL, mirroring PARMEM_MCP_URL's shape
        self.requests: list[str] = []
