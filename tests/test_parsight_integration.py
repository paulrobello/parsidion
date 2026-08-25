"""Env-gated end-to-end test against a REAL parsight daemon (spec §10).

Skipped unless PARSIDION_TEST_PARSIGHT=1. Requires the real `parsight` binary
on PATH and its daemon running on the default port.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import parsight_backend  # noqa: E402

EXPECTED_KEYS = {
    "score",
    "stem",
    "title",
    "folder",
    "tags",
    "path",
    "summary",
    "note_type",
    "project",
    "confidence",
    "mtime",
    "related",
    "is_stale",
    "incoming_links",
}


@pytest.mark.skipif(
    os.environ.get("PARSIDION_TEST_PARSIGHT") != "1",
    reason="requires a real parsight daemon (set PARSIDION_TEST_PARSIGHT=1)",
)
@pytest.mark.timeout(120)
def test_real_parsight_end_to_end(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    vault_search = importlib.import_module("vault_search")

    # Undo the suite-wide isolation: talk to the real daemon.
    monkeypatch.setenv("PARSIGHT_MCP_URL", "http://127.0.0.1:4848/mcp")
    parsight_backend.reset_parsight_cache()

    note = tmp_vault / "Patterns" / "integration-probe-note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntags: [integration, parsight]\ntype: pattern\n---\n"
        "# Integration Probe Note\nA uniquely phrased zanzibar-quokka sentence.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(tmp_vault)], check=True, timeout=30)

    index = subprocess.run(
        ["parsight", "index", str(tmp_vault)],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert index.returncode == 0, index.stderr

    results = vault_search.search(
        "zanzibar quokka sentence", vault=tmp_vault, backend="parsight"
    )
    assert results, "parsight returned no hits for the probe note"
    assert set(results[0].keys()) == EXPECTED_KEYS
    assert results[0]["stem"] == "integration-probe-note"
