"""ARC-004: contract tests for the shared index-rebuild subprocess owner.

``core.vault_index.run_index_rebuild`` replaced three independent launchers
(installer, MCP ``rebuild_index``, summarizer queue) whose argv/env/discovery
contracts had drifted — most importantly the installer copy omitted
``--no-project``. These tests pin the unified contract.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import core.vault_index as vi


def _capture_run(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_run(cmd, *, cwd, timeout, env=None, stdin=None):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["timeout"] = timeout
        captured["env"] = env
        return (
            "ok",
            subprocess.CompletedProcess(cmd, 0, stdout="rebuilt", stderr=""),
        )

    monkeypatch.setattr(vi, "run_with_pgkill", fake_run)
    return captured


def test_argv_always_no_project_and_vault(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    reason, proc = vi.run_index_rebuild(tmp_path)

    assert reason == "ok"
    assert proc is not None and proc.returncode == 0
    cmd = captured["cmd"]
    assert cmd[0:3] == ["uv", "run", "--no-project"]
    assert cmd[3].endswith("update_index.py")
    assert cmd[4:] == ["--vault", str(tmp_path)]
    assert captured["cwd"] == tmp_path


def test_no_vault_omits_flag(monkeypatch):
    captured = _capture_run(monkeypatch)
    vi.run_index_rebuild()

    cmd = captured["cmd"]
    assert "--vault" not in cmd


def test_graph_flags_appended(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    vi.run_index_rebuild(
        tmp_path, rebuild_graph=True, graph_include_daily=True, timeout=42.0
    )

    cmd = captured["cmd"]
    assert cmd[-2:] == ["--rebuild-graph", "--graph-include-daily"]
    assert captured["timeout"] == 42.0


def test_env_strips_claudecode(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    vi.run_index_rebuild(tmp_path)

    env = captured["env"]
    assert isinstance(env, dict)
    assert "CLAUDECODE" not in env


def test_explicit_scripts_dir_used_exclusively(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    # Empty dir: update_index.py missing -> launch failure, no fallback.
    reason, proc = vi.run_index_rebuild(scripts_dir=tmp_path)
    assert (reason, proc) == ("launch", None)
    assert "cmd" not in captured


def test_explicit_scripts_dir_resolved(monkeypatch, tmp_path):
    captured = _capture_run(monkeypatch)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "update_index.py").write_text("# stub\n", encoding="utf-8")

    vi.run_index_rebuild(scripts_dir=scripts_dir)

    assert captured["cmd"][3] == str(scripts_dir / "update_index.py")


def test_default_discovery_finds_a_script(monkeypatch):
    captured = _capture_run(monkeypatch)
    vi.run_index_rebuild()

    script = Path(captured["cmd"][3])
    assert script.name == "update_index.py"
    # Source checkout first (ARC-021: same code as the running process),
    # installed SCRIPTS_DIR second.
    assert script.parent == Path(vi.__file__).resolve().parent.parent or (
        script.parent == vi.SCRIPTS_DIR
    )
