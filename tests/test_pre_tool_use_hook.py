"""Tests for pre_tool_use_hook.py (PreToolUse file-scoped vault recall).

Covers the Claude Code contract: file_path extraction, the distinct-token
relevance gate, per-file result caching, parsight probe negative caching,
budget truncation, and the never-block guarantee (malformed stdin / raising
observability still print ``{}`` and exit 0).

ARC-006 discipline: parsight internals are patched on the implementation
module (``core.parsight_backend``), not the root shim.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import pre_tool_use_hook  # noqa: E402
from core import parsight_backend  # noqa: E402 -- ARC-006: patch where it lives

TARGET_FILE = "/Users/dev/Repos/myproj/parsidion/vault_search.py"
MATCHING_NOTE: dict[str, object] = {
    "stem": "vault-search-decay-ordering",
    "title": "Vault search decay ordering",
    "folder": "Debugging",
    "tags": ["vault", "search"],
    "path": "",
    "summary": "Decay is applied after score aggregation, not per hit.",
    "project": "myproj",
    "note_type": "debugging",
    "mtime": 1788000000,
}
UNRELATED_NOTE: dict[str, object] = {
    "stem": "quantum-banana-yodeling",
    "title": "Quantum banana yodeling",
    "folder": "Knowledge",
    "tags": ["banana"],
    "path": "",
    "summary": "Unrelated.",
    "project": "other",
    "note_type": "knowledge",
    "mtime": 1788000001,
}


def _payload(file_path: str = TARGET_FILE, tool: str = "Read") -> dict:
    return {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
        "cwd": "/Users/dev/Repos/myproj",
        "session_id": "sess-1",
    }


@pytest.fixture()
def _isolated_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the per-file cache and probe stamp to a temp dir."""
    logs = tmp_path / "logs"
    monkeypatch.setattr(pre_tool_use_hook, "secure_log_dir", lambda: logs)
    return logs


@pytest.fixture()
def hook_env(
    tmp_vault: Path,
    _isolated_logs: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, list[object]]:
    """Local leg returns the matching note; parsight probe fails (local-only)."""
    calls: dict[str, list[object]] = {"scan": [], "search": [], "probe": []}

    def fake_scan(vault: Path | None = None, limit: int = 5000):
        calls["scan"].append(vault)
        return [dict(MATCHING_NOTE), dict(UNRELATED_NOTE)]

    def fake_probe(vault: Path | None = None) -> bool:
        calls["probe"].append(vault)
        return False

    monkeypatch.setattr(pre_tool_use_hook, "load_note_index_metadata", fake_scan)
    monkeypatch.setattr(parsight_backend, "resolve_parsight_backend", fake_probe)
    monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
    return calls


class TestInjection:
    def test_read_with_matching_note_injects(
        self, hook_env: dict[str, list[object]]
    ) -> None:
        result = pre_tool_use_hook.run_injection(_payload())
        assert result != {}
        output = result["hookSpecificOutput"]
        assert output["hookEventName"] == "PreToolUse"
        context = output["additionalContext"]
        assert "Vault search decay ordering" in context
        assert "<content>" in context  # SEC-108 untrusted framing
        # The unrelated note is gated out by the term gate.
        assert "Quantum banana" not in context

    def test_unrelated_file_gated_out(self, hook_env: dict[str, list[object]]) -> None:
        result = pre_tool_use_hook.run_injection(
            _payload(file_path="/Users/dev/Repos/myproj/xqzvw/qrzt.py")
        )
        assert result == {}

    def test_min_term_matches_zero_disables_gate(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        (tmp_vault / "config.yaml").write_text(
            "pre_tool_use_hook:\n  min_term_matches: 0\n", encoding="utf-8"
        )
        result = pre_tool_use_hook.run_injection(
            _payload(file_path="/Users/dev/Repos/myproj/xqzvw/qrzt.py")
        )
        context = result["hookSpecificOutput"]["additionalContext"]
        assert "Quantum banana yodeling" in context

    def test_edit_tool_supported(self, hook_env: dict[str, list[object]]) -> None:
        result = pre_tool_use_hook.run_injection(_payload(tool="Edit"))
        assert result != {}

    def test_missing_file_path_returns_empty(
        self, hook_env: dict[str, list[object]]
    ) -> None:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "cwd": "/Users/dev/Repos/myproj",
        }
        assert pre_tool_use_hook.run_injection(payload) == {}

    def test_malformed_tool_input_returns_empty(
        self, hook_env: dict[str, list[object]]
    ) -> None:
        payload = {"tool_name": "Read", "tool_input": "not-a-dict"}
        assert pre_tool_use_hook.run_injection(payload) == {}

    def test_file_inside_vault_skips(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        result = pre_tool_use_hook.run_injection(
            _payload(file_path=str(tmp_vault / "Patterns" / "some-note.md"))
        )
        assert result == {}
        assert hook_env["scan"] == []  # no scan even attempted

    def test_enabled_false_short_circuits(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        (tmp_vault / "config.yaml").write_text(
            "pre_tool_use_hook:\n  enabled: false\n", encoding="utf-8"
        )
        assert pre_tool_use_hook.run_injection(_payload()) == {}
        assert hook_env["scan"] == []

    def test_budget_truncation_respected(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        (tmp_vault / "config.yaml").write_text(
            "pre_tool_use_hook:\n  max_chars: 400\n", encoding="utf-8"
        )
        result = pre_tool_use_hook.run_injection(_payload())
        context = result["hookSpecificOutput"]["additionalContext"]
        assert len(context) <= 400
        assert "Vault search decay ordering" in context

    def test_index_unavailable_fails_open(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """DB absent (scan None) + probe down => {} and exit-0 shape, no raise."""
        monkeypatch.setattr(
            pre_tool_use_hook, "load_note_index_metadata", lambda *a, **k: None
        )
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda *a, **k: False
        )
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
        assert pre_tool_use_hook.run_injection(_payload()) == {}

    def test_write_hook_event_failure_never_blocks(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(*a: object, **k: object) -> None:
            raise RuntimeError("log write failed")

        monkeypatch.setattr(
            pre_tool_use_hook,
            "load_note_index_metadata",
            lambda *a, **k: [dict(MATCHING_NOTE)],
        )
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda *a, **k: False
        )
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", boom)
        result = pre_tool_use_hook.run_injection(_payload())
        assert result != {}  # injection still delivered

    def test_injection_event_carries_stage_deltas(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events: list[dict] = []

        def fake_event(**kwargs: object) -> None:
            events.append(dict(kwargs))

        monkeypatch.setattr(
            pre_tool_use_hook,
            "load_note_index_metadata",
            lambda *a, **k: [dict(MATCHING_NOTE)],
        )
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda *a, **k: False
        )
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", fake_event)
        pre_tool_use_hook.run_injection(_payload())
        assert len(events) == 1
        assert events[0]["hook"] == "PreToolUse"
        stages = events[0]["stages_ms"]
        assert isinstance(stages, dict) and stages


class TestParsightLeg:
    @pytest.fixture()
    def semantic_env(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> dict[str, list[object]]:
        """Probe passes; search returns a semantically-matched note."""
        calls: dict[str, list[object]] = {"search": [], "probe": []}

        def fake_probe(vault: Path | None = None) -> bool:
            calls["probe"].append(vault)
            return True

        def fake_search(
            query: str,
            top_k: int = 10,
            vault: Path | None = None,
            timeout: float | None = None,
            kill_grace_secs: float | None = None,
        ) -> list[dict[str, object]]:
            calls["search"].append(query)
            return [
                {
                    "stem": "semantic-dedup-note",
                    "title": "Semantic deduplication threshold",
                    "folder": "Patterns",
                    "tags": ["dedup", "search"],
                    "path": "",
                    "summary": "Dedup uses cosine similarity at 0.80.",
                }
            ]

        monkeypatch.setattr(
            pre_tool_use_hook, "load_note_index_metadata", lambda *a, **k: []
        )
        monkeypatch.setattr(parsight_backend, "resolve_parsight_backend", fake_probe)
        monkeypatch.setattr(parsight_backend, "parsight_search", fake_search)
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
        return calls

    def test_semantic_note_injected_when_local_scan_empty(
        self, semantic_env: dict[str, list[object]]
    ) -> None:
        result = pre_tool_use_hook.run_injection(_payload())
        context = result["hookSpecificOutput"]["additionalContext"]
        assert "Semantic deduplication threshold" in context
        assert semantic_env["search"], "parsight leg must run"

    def test_probe_failure_negative_cached(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        probes = {"n": 0}

        def fake_probe(vault: Path | None = None) -> bool:
            probes["n"] += 1
            return False

        monkeypatch.setattr(
            pre_tool_use_hook, "load_note_index_metadata", lambda *a, **k: []
        )
        monkeypatch.setattr(parsight_backend, "resolve_parsight_backend", fake_probe)
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
        assert pre_tool_use_hook.run_injection(_payload()) == {}
        assert probes["n"] == 1
        # Second file (different cache key), stamp still fresh: no new probe.
        assert (
            pre_tool_use_hook.run_injection(
                _payload(file_path="/Users/dev/Repos/myproj/other_module.py")
            )
            == {}
        )
        assert probes["n"] == 1
        assert (_isolated_logs / "parsidion-ptu-probe").exists()

    def test_parsight_disabled_config_skips_search(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        searches: list[str] = []

        def fake_search(*a: object, **k: object) -> list[dict[str, object]]:
            searches.append(str(a))
            return []

        (tmp_vault / "config.yaml").write_text(
            "pre_tool_use_hook:\n  parsight: false\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            pre_tool_use_hook, "load_note_index_metadata", lambda *a, **k: []
        )
        monkeypatch.setattr(parsight_backend, "parsight_search", fake_search)
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
        assert pre_tool_use_hook.run_injection(_payload()) == {}
        assert searches == []

    def test_recall_timeout_oversized_config_clamped(
        self,
        tmp_vault: Path,
        _isolated_logs: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_search(
            query: str,
            top_k: int = 10,
            vault: Path | None = None,
            timeout: float | None = None,
            kill_grace_secs: float | None = None,
        ) -> list[dict[str, object]]:
            captured["timeout"] = timeout
            return []

        (tmp_vault / "config.yaml").write_text(
            "pre_tool_use_hook:\n  recall_timeout_s: 60.0\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            pre_tool_use_hook, "load_note_index_metadata", lambda *a, **k: []
        )
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda *a, **k: True
        )
        monkeypatch.setattr(parsight_backend, "parsight_search", fake_search)
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)
        pre_tool_use_hook.run_injection(_payload())
        assert captured["timeout"] == 5.0  # clamped ceiling


class TestPerFileCache:
    def test_second_read_served_from_cache(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        first = pre_tool_use_hook.run_injection(_payload())
        second = pre_tool_use_hook.run_injection(_payload())
        assert first == second
        assert len(hook_env["scan"]) == 1  # scan ran once

    def test_negative_result_cached(
        self, tmp_vault: Path, hook_env: dict[str, list[object]]
    ) -> None:
        miss_payload = _payload(file_path="/Users/dev/Repos/myproj/xqzvw/qrzt.py")
        assert pre_tool_use_hook.run_injection(miss_payload) == {}
        assert pre_tool_use_hook.run_injection(miss_payload) == {}
        assert len(hook_env["scan"]) == 1  # miss cached too

    def test_cache_file_written_under_log_dir(
        self,
        tmp_vault: Path,
        hook_env: dict[str, list[object]],
        _isolated_logs: Path,
    ) -> None:
        pre_tool_use_hook.run_injection(_payload())
        cache_dir = _isolated_logs / "parsidion-ptu-cache"
        assert cache_dir.is_dir()
        assert len(list(cache_dir.glob("*.json"))) == 1


class TestCodexPatchTarget:
    """Codex apply_patch payloads resolve to their first touched file."""

    def test_update_target_resolved_against_cwd(self) -> None:
        path = pre_tool_use_hook._extract_file_path(
            "apply_patch",
            {"command": "*** Begin Patch\n*** Update File: src/app.py\n@@\n"},
            cwd="/repo",
        )
        assert path == Path("/repo/src/app.py")

    def test_absolute_target_not_rejoined(self) -> None:
        path = pre_tool_use_hook._extract_file_path(
            "apply_patch",
            {"command": "*** Begin Patch\n*** Add File: /abs/x.py\n"},
            cwd="/repo",
        )
        assert path == Path("/abs/x.py")

    def test_move_patch_first_target_is_updated_source(self) -> None:
        # Realistic V4A move: Update + Move-to. The source file is the first
        # touched path in patch order, so recall keys on it.
        path = pre_tool_use_hook._extract_file_path(
            "apply_patch",
            {
                "command": (
                    "*** Begin Patch\n*** Update File: old.txt\n*** Move to: new.txt\n"
                )
            },
            cwd="/repo",
        )
        assert path == Path("/repo/old.txt")

    def test_delete_only_patch_yields_none(self) -> None:
        assert (
            pre_tool_use_hook._extract_file_path(
                "apply_patch",
                {"command": "*** Begin Patch\n*** Delete File: a.txt\n"},
                cwd="/repo",
            )
            is None
        )

    def test_non_patch_command_yields_none(self) -> None:
        assert (
            pre_tool_use_hook._extract_file_path(
                "apply_patch", {"command": "echo hi"}, cwd="/repo"
            )
            is None
        )

    def test_run_injection_survives_apply_patch_payload(self) -> None:
        assert (
            pre_tool_use_hook.run_injection(
                {
                    "tool_name": "apply_patch",
                    "tool_input": {"command": "*** Update File: src/x.py\n"},
                    "cwd": "/repo",
                }
            )
            == {}
        )


class TestMainContract:
    def test_malformed_stdin_prints_empty_exit_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", _FakeStdin("{not json"))
        assert pre_tool_use_hook.main() == 0
        assert json.loads(capsys.readouterr().out) == {}

    def test_empty_stdin_prints_empty(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", _FakeStdin(""))
        assert pre_tool_use_hook.main() == 0
        assert json.loads(capsys.readouterr().out) == {}

    def test_run_injection_never_raises_on_garbage(self) -> None:
        assert pre_tool_use_hook.run_injection({"tool_name": 42}) == {}
        assert pre_tool_use_hook.run_injection(None) == {}  # type: ignore[arg-type]


class _FakeStdin:
    """Minimal stdin stand-in exposing read()."""

    def __init__(self, data: str) -> None:
        self._data = data

    def read(self) -> str:
        return self._data


class TestRegistration:
    """Installer wiring: matcher, timeout, and the single-source script map."""

    def test_hook_script_map_carries_pretooluse(self) -> None:
        import agent_adapter

        assert (
            agent_adapter._CLAUDE_HOOK_SCRIPTS["PreToolUse"] == "pre_tool_use_hook.py"
        )
        assert (_SCRIPTS_DIR / "pre_tool_use_hook.py").is_file()

    def test_hook_options_and_matchers(self) -> None:
        from installer.paths import _HOOK_MATCHERS, _HOOK_OPTIONS

        assert _HOOK_MATCHERS["PreToolUse"] == "Read|Edit"
        # Host timeout must clear the hook's self-bound (search budget +
        # kill grace + startup/probe slack) so {} always beats the kill.
        budget = float(pre_tool_use_hook._DEFAULTS["recall_timeout_s"])
        grace = pre_tool_use_hook._SEARCH_KILL_GRACE_S
        assert _HOOK_OPTIONS["PreToolUse"]["timeout"] > (budget + grace + 2.5) * 1000

    def test_merge_hooks_registers_pretooluse_with_matcher(
        self, tmp_path: Path
    ) -> None:
        import installer.hooks as installer_hooks

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        merged = json.loads(settings_file.read_text(encoding="utf-8"))
        ptu_entries = merged["hooks"]["PreToolUse"]
        assert len(ptu_entries) == 1
        entry = ptu_entries[0]
        assert entry["matcher"] == "Read|Edit"
        handler = entry["hooks"][0]
        assert handler["type"] == "command"
        assert handler["command"].endswith("pre_tool_use_hook.py")
        assert handler["timeout"] == 10000

    def test_merge_hooks_upgrades_legacy_matcher_less_entry(
        self, tmp_path: Path
    ) -> None:
        """A pre-matcher install registers matcher ""; reinstall raises it."""
        import installer.hooks as installer_hooks

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        legacy_command = installer_hooks._build_managed_command(
            installer_hooks._spec(installer_hooks._adapter("claude")),
            claude_dir,
            "PreToolUse",
        )
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": legacy_command,
                                        "timeout": 10000,
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )

        installer_hooks.merge_hooks(
            claude_dir, settings_file, dry_run=False, verbose=False
        )

        merged = json.loads(settings_file.read_text(encoding="utf-8"))
        assert merged["hooks"]["PreToolUse"][0]["matcher"] == "Read|Edit"
