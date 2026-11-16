"""``summarize_sessions.py --all-vaults``: one run maintains every registered vault."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_summarizer_queue_fixes import _fresh_summarize_sessions


def _register(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    default: Path,
    named: dict[str, Path],
) -> None:
    home = tmp_path / "home"
    cfg = home / ".config" / "parsidion"
    cfg.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_VAULT", raising=False)
    lines = ["vaults:"] + [f"  {n}: {p}" for n, p in named.items()]
    lines += ["", f"default: {default}", ""]
    (cfg / "vaults.yaml").write_text("\n".join(lines), encoding="utf-8")
    import parsidion.core.vault_path as vp

    vp.resolve_vault.cache_clear()  # pyright: ignore[reportFunctionMemberAccess]
    monkeypatch.chdir(tmp_path)


def test_iter_registered_vaults_dedupes_and_skips_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    main_v = tmp_path / "main"
    personal = tmp_path / "personal"
    main_v.mkdir()
    personal.mkdir()
    _register(
        tmp_path,
        monkeypatch,
        main_v,
        {"main": main_v, "personal": personal, "stale": tmp_path / "gone"},
    )

    assert mod._iter_registered_vaults() == [main_v.resolve(), personal.resolve()]


def test_all_vaults_processes_each_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    vaults = {"main": tmp_path / "main", "personal": tmp_path / "personal"}
    for name, v in vaults.items():
        v.mkdir()
        (v / "pending_summaries.jsonl").write_text(
            json.dumps({"session_id": f"s-{name}", "transcript_path": "x"}) + "\n",
            encoding="utf-8",
        )
    _register(tmp_path, monkeypatch, vaults["main"], vaults)

    seen: list[Path] = []

    async def fake_run_all(entries: list[dict[str, Any]], *args: Any) -> list[Any]:
        vault = next(a for a in args if isinstance(a, Path))
        seen.append(vault)
        note = vault / "Patterns" / f"{entries[0]['session_id']}.md"
        note.parent.mkdir(exist_ok=True)
        note.write_text("---\ntype: pattern\n---\n# n\n", encoding="utf-8")
        return [(entries[0], note)]

    monkeypatch.setattr(mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        mod.anyio, "run", lambda fn, *a: asyncio.run(fn(*a)), raising=False
    )
    monkeypatch.setattr(
        mod.vault_hooks, "apply_configured_env_defaults", lambda **_: None
    )
    monkeypatch.setattr(sys, "argv", ["summarize_sessions.py", "--all-vaults"])

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 0
    assert {p.resolve() for p in seen} == {v.resolve() for v in vaults.values()}
    for name, v in vaults.items():
        assert (v / "Patterns" / f"s-{name}.md").exists()


def test_all_vaults_conflicts_with_sessions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    monkeypatch.setattr(
        sys, "argv", ["summarize_sessions.py", "--all-vaults", "--sessions", "f.jsonl"]
    )
    with pytest.raises(SystemExit) as exc:
        mod.main()
    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


def test_all_vaults_isolates_per_vault_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    vaults = {"main": tmp_path / "main", "personal": tmp_path / "personal"}
    for v in vaults.values():
        v.mkdir()
    _register(tmp_path, monkeypatch, vaults["main"], vaults)

    processed: list[Path] = []

    def fake_process(vault_path: Path, args: Any) -> int:
        if vault_path == vaults["main"].resolve():
            raise PermissionError("boom")
        processed.append(vault_path)
        return 0

    monkeypatch.setattr(mod, "_process_vault", fake_process)
    monkeypatch.setattr(sys, "argv", ["summarize_sessions.py", "--all-vaults"])

    with pytest.raises(SystemExit) as exc:
        mod.main()

    assert exc.value.code == 1
    assert processed == [vaults["personal"].resolve()]
    assert str(vaults["main"].resolve()) in capsys.readouterr().err


def test_iter_registered_vaults_warns_on_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    main_v = tmp_path / "main"
    main_v.mkdir()
    gone = tmp_path / "gone"
    _register(tmp_path, monkeypatch, main_v, {"main": main_v, "stale": gone})

    mod._iter_registered_vaults()

    assert str(gone.resolve()) in capsys.readouterr().err


def test_all_vaults_isolates_settings_between_vaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _fresh_summarize_sessions(monkeypatch)
    vaults = {"main": tmp_path / "main", "personal": tmp_path / "personal"}
    for name, v in vaults.items():
        v.mkdir()
        (v / "pending_summaries.jsonl").write_text(
            json.dumps({"session_id": f"s-{name}", "transcript_path": "x"}) + "\n",
            encoding="utf-8",
        )
        (v / "config.yaml").write_text(
            f"summarizer:\n  model: model-{name}\n"
            f"anthropic_env:\n  ANTHROPIC_DEFAULT_HAIKU_MODEL: haiku-{name}\n",
            encoding="utf-8",
        )
    _register(tmp_path, monkeypatch, vaults["main"], vaults)
    monkeypatch.delenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", raising=False)

    import os

    from parsidion.core.vault_path import resolve_vault

    seen: dict[str, tuple[Any, str | None, Path]] = {}

    async def fake_run_all(entries: list[dict[str, Any]], *args: Any) -> list[Any]:
        vault = next(a for a in args if isinstance(a, Path))
        seen[vault.name] = (
            args[0],
            os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL"),
            resolve_vault(),
        )
        note = vault / "Patterns" / f"{entries[0]['session_id']}.md"
        note.parent.mkdir(exist_ok=True)
        note.write_text("---\ntype: pattern\n---\n# n\n", encoding="utf-8")
        return [(entries[0], note)]

    monkeypatch.setattr(mod, "run_all", fake_run_all)
    monkeypatch.setattr(
        mod.anyio, "run", lambda fn, *a: asyncio.run(fn(*a)), raising=False
    )
    monkeypatch.setattr(sys, "argv", ["summarize_sessions.py", "--all-vaults"])

    with pytest.raises(SystemExit):
        mod.main()

    for name, v in vaults.items():
        model, haiku, scoped = seen[name]
        assert model == f"model-{name}"
        assert haiku == f"haiku-{name}"
        assert scoped.resolve() == v.resolve()
    assert os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") is None
