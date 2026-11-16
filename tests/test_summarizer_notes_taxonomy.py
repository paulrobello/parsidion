"""Summarizer note routing follows the per-vault taxonomy; the background
spawn passes the resolved vault explicitly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from parsidion import agent_pipeline
from parsidion.core.vault_config import clear_config_cache
from parsidion.summarizer.notes import write_note_with_reason

_NOTE = (
    "---\ndate: 2026-06-16\ntype: knowledge\ntags: [knowledge]\n"
    'related: ["[[real-note]]"]\n'
    "---\n# Taxonomy Routed Note\n\nBody.\n"
)


def test_write_note_uses_taxonomy_folder(tmp_vault: Path) -> None:
    (tmp_vault / "config.yaml").write_text(
        "taxonomy:\n"
        "  folders: Home, Reference, Daily\n"
        "  type_folder.knowledge: Reference\n"
        "  default_folder: Home\n",
        encoding="utf-8",
    )
    clear_config_cache()
    path, reason = write_note_with_reason(_NOTE, False, tmp_vault)
    assert reason == ""
    assert path is not None
    assert path.parent == tmp_vault / "Reference"


def test_write_note_without_taxonomy_uses_builtin_folder(tmp_vault: Path) -> None:
    clear_config_cache()
    path, reason = write_note_with_reason(_NOTE, False, tmp_vault)
    assert reason == ""
    assert path is not None
    assert path.parent == tmp_vault / "Knowledge"


def test_launch_summarizer_passes_vault_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "pending_summaries.jsonl").write_text(
        '{"session_id": "s1", "transcript_path": "/tmp/x.jsonl"}\n',
        encoding="utf-8",
    )
    log_dir = tmp_path / "logs"

    def fake_secure_log_dir() -> Path:
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    monkeypatch.setattr(agent_pipeline, "secure_log_dir", fake_secure_log_dir)
    captured: dict[str, Any] = {}

    def fake_popen(cmd: list[str], **kwargs: Any) -> None:
        captured["cmd"] = cmd

    monkeypatch.setattr(agent_pipeline.subprocess, "Popen", fake_popen)
    agent_pipeline._launch_summarizer_if_pending(vault)

    cmd = captured["cmd"]
    assert cmd[cmd.index("-V") + 1] == str(vault)
