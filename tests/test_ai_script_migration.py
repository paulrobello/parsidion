from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import embed_eval_generate  # noqa: E402
import run_trigger_eval  # noqa: E402
import vault_merge  # noqa: E402


def test_vault_merge_uses_large_tier_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    path_a = tmp_path / "a.md"
    path_b = tmp_path / "b.md"
    path_a.write_text("# A\n\nAlpha content.", encoding="utf-8")
    path_b.write_text("# B\n\nBeta content.", encoding="utf-8")

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "## Summary\n\nMerged content from both notes with enough length."

    monkeypatch.setattr(vault_merge.ai_backend, "run_ai_prompt", fake_run_ai_prompt)

    result = vault_merge._ai_merge_bodies(
        path_a, path_b, "Merged Note", vault_path=tmp_path
    )

    assert result == "## Summary\n\nMerged content from both notes with enough length."
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "large"
    assert calls[0]["purpose"] == "vault-merge"
    assert calls[0]["vault"] == tmp_path


def test_run_trigger_eval_uses_small_tier_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "YES"

    monkeypatch.setattr(
        run_trigger_eval.ai_backend, "run_ai_prompt", fake_run_ai_prompt
    )

    assert run_trigger_eval.run_single_query("query", "skill", "description") is True
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["timeout"] == 30
    assert calls[0]["purpose"] == "trigger-eval"


def test_embed_eval_generate_uses_small_tier_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[dict[str, object]] = []
    note = tmp_path / "note.md"
    note.write_text("---\ntags: [python]\n---\n# Note\n\nBody text", encoding="utf-8")

    def fake_run_ai_prompt(prompt: str, **kwargs: object) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return '{"queries": ["query one", "query two"]}'

    monkeypatch.setattr(
        embed_eval_generate.ai_backend, "run_ai_prompt", fake_run_ai_prompt
    )

    result = embed_eval_generate.generate_queries_for_note(note, 2)

    assert result == ["query one", "query two"]
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "embed-eval-generate"
