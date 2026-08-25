"""Smoke tests for the fake parsight fixture itself (Task 1).

These prove the fixture contract every later parsight test depends on:
PATH-front resolution, argv+cwd recording, canned JSON per subcommand,
global and per-subcommand exit codes, delays, stdout override, and the
/health endpoint + PARSIGHT_MCP_URL wiring.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from tests.fake_parsight import FakeHealth, FakeParsight


def _run(
    fake: FakeParsight, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(fake.binary_path), *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        timeout=8,
    )


def test_binary_resolves_first_on_path(fake_parsight: FakeParsight) -> None:
    which = shutil.which("parsight")
    assert which is not None
    assert Path(which) == fake_parsight.binary_path


def test_find_code_emits_canned_json_and_records_argv_and_cwd(
    fake_parsight: FakeParsight, tmp_path: Path
) -> None:
    payload = {"results": [{"file_path": "a.md", "score": 0.05}], "_meta": {}}
    fake_parsight.configure(find_code=payload)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    proc = _run(
        fake_parsight, "find-code", "my query", "--json", "--limit", "5", cwd=workdir
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == payload
    call = fake_parsight.wait_for_call("find-code")
    assert call["argv"] == ["find-code", "my query", "--json", "--limit", "5"]
    assert Path(str(call["cwd"])).resolve() == workdir.resolve()


def test_find_code_default_payload_is_results_shape(
    fake_parsight: FakeParsight,
) -> None:
    proc = _run(fake_parsight, "find-code", "q", "--json", "--limit", "5")
    assert json.loads(proc.stdout) == {"results": []}


def test_repos_default_payload(fake_parsight: FakeParsight) -> None:
    proc = _run(fake_parsight, "repos", "--json")
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"repositories": [], "_meta": {"count": 0}}


def test_global_and_per_subcommand_exit_codes(fake_parsight: FakeParsight) -> None:
    fake_parsight.configure(exit_code=3)
    assert _run(fake_parsight, "repos", "--json").returncode == 3
    fake_parsight.configure(exit_code=0, exit_codes={"find-code": 1})
    assert _run(fake_parsight, "repos", "--json").returncode == 0
    assert (
        _run(fake_parsight, "find-code", "q", "--json", "--limit", "5").returncode == 1
    )


def test_delay_is_honored(fake_parsight: FakeParsight) -> None:
    fake_parsight.configure(delay=0.4)
    start = time.monotonic()
    _run(fake_parsight, "repos", "--json")
    assert time.monotonic() - start >= 0.4


def test_stdout_override_is_verbatim(fake_parsight: FakeParsight) -> None:
    fake_parsight.configure(stdout_override="this is not json {")
    proc = _run(fake_parsight, "find-code", "q", "--json", "--limit", "5")
    assert proc.stdout == "this is not json {"


def test_assert_no_call_detects_absence(fake_parsight: FakeParsight) -> None:
    fake_parsight.assert_no_call("index", settle=0.1)
    _run(fake_parsight, "index", "/tmp/x", "--json")
    try:
        fake_parsight.assert_no_call("index", settle=0.1)
    except AssertionError:
        return
    raise AssertionError("assert_no_call should have flagged the index call")


def test_health_server_answers_200_and_sets_env(
    fake_parsight_health: FakeHealth,
) -> None:
    assert os.environ["PARSIGHT_MCP_URL"] == fake_parsight_health.url
    health_url = fake_parsight_health.url.rsplit("/", 1)[0] + "/health"
    with urllib.request.urlopen(health_url, timeout=2) as resp:
        assert resp.status == 200
    assert "/health" in fake_parsight_health.requests
