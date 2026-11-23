"""Tests for scripts/check_doc_links.py — the broken-doc-links gate (O1)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_doc_links.py"
_spec = importlib.util.spec_from_file_location("check_doc_links", _SCRIPT)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def _row(src: str, target: str = "missing.py", line: int = 1) -> dict[str, Any]:
    return {
        "confidence": "high",
        "link_line": line,
        "raw_target": target,
        "reason": "target-not-found",
        "src_file_path": src,
    }


def _meta(**overrides: Any) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "stale": False,
        "partial": False,
        "root_missing": False,
        "head_unreadable": False,
        "extractor_stale": False,
        "empty_reason": None,
    }
    meta.update(overrides)
    return meta


class TestEvaluate:
    def test_fail_on_actionable_rows(self):
        verdict, detail = gate.evaluate(_meta(), [_row("docs/README.md")])
        assert verdict == "fail"
        assert "docs/README.md:1 -> missing.py" in detail

    def test_fixture_source_is_excluded(self):
        verdict, detail = gate.evaluate(
            _meta(), [_row("tests/fixtures/prompts/golden/x.content.md", "[[sqlite]]")]
        )
        assert verdict == "clean"
        assert "1 excluded" in detail

    def test_mixed_rows_fail_with_exclusion_count(self):
        verdict, _ = gate.evaluate(
            _meta(),
            [_row("docs/README.md"), _row("tests/fixtures/golden/y.md")],
        )
        assert verdict == "fail"

    def test_skip_when_stale(self):
        verdict, detail = gate.evaluate(_meta(stale=True), [])
        assert verdict == "skip"
        assert "stale" in detail

    @pytest.mark.parametrize(
        "key", ["partial", "root_missing", "head_unreadable", "extractor_stale"]
    )
    def test_skip_on_every_untrustworthy_flag(self, key):
        verdict, _ = gate.evaluate(_meta(**{key: True}), [])
        assert verdict == "skip"

    def test_skip_on_empty_reason(self):
        verdict, detail = gate.evaluate(_meta(empty_reason="no-repo"), [])
        assert verdict == "skip"
        assert "no-repo" in detail

    def test_skip_without_meta(self):
        verdict, _ = gate.evaluate(None, [])
        assert verdict == "skip"

    def test_clean_on_healthy_empty_payload(self):
        verdict, detail = gate.evaluate(_meta(), [])
        assert verdict == "clean"
        assert "0 actionable" in detail


class TestMainCli:
    def _run(self, monkeypatch, code_out):
        monkeypatch.setattr(gate, "_run_parsight", lambda *a, **k: code_out)
        return gate.main(["--root", str(Path("/tmp/whatever"))])

    def test_unreachable_daemon_skips(self, monkeypatch, capsys):
        assert self._run(monkeypatch, (2, "")) == 0
        assert "SKIP" in capsys.readouterr().err

    def test_garbage_output_skips(self, monkeypatch, capsys):
        assert self._run(monkeypatch, (0, "not json")) == 0
        assert "SKIP" in capsys.readouterr().err

    def test_broken_payload_fails(self, monkeypatch, capsys):
        payload = json.dumps(
            {"_meta": _meta(), "broken_links": [_row("docs/README.md")]}
        )
        assert self._run(monkeypatch, (0, payload)) == 1
        assert "FAIL" in capsys.readouterr().out

    def test_clean_payload_passes(self, monkeypatch, capsys):
        payload = json.dumps({"_meta": _meta(), "broken_links": []})
        assert self._run(monkeypatch, (0, payload)) == 0
        assert "OK" in capsys.readouterr().out

    def test_timeout_skips(self, monkeypatch, capsys):
        def raise_timeout(*a):
            raise subprocess.TimeoutExpired(cmd="parsight", timeout=30)

        monkeypatch.setattr(gate, "_run_parsight", raise_timeout)
        assert gate.main([]) == 0
        assert "timed out" in capsys.readouterr().err

    def test_missing_cli_skips(self, monkeypatch, capsys):
        def raise_missing(*a):
            raise FileNotFoundError("parsight")

        monkeypatch.setattr(gate, "_run_parsight", raise_missing)
        assert gate.main([]) == 0
        assert "not found" in capsys.readouterr().err


class TestRepoSlug:
    def test_strips_git_suffix(self, monkeypatch):
        monkeypatch.setattr(
            gate.subprocess,
            "run",
            lambda *a, **k: _Completed(
                stdout="git@github.com:paulrobello/parsidion.git\n"
            ),
        )
        assert gate._repo_slug(Path("/repo")) == "parsidion"

    def test_falls_back_to_dirname_without_origin(self, monkeypatch):
        def fail(*a, **k):
            raise subprocess.SubprocessError("no origin")

        monkeypatch.setattr(gate.subprocess, "run", fail)
        assert gate._repo_slug(Path("/repos/MyRepo")) == "MyRepo"


class _Completed:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_fixture_prefix_matches_golden_layout():
    assert "tests/fixtures/prompts/golden/repair-frontmatter/x.md".startswith(
        gate.EXCLUDED_SOURCE_PREFIXES
    )
