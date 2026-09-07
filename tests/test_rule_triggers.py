"""Tests for trigger-scoped rule notes (``type: rule``).

Covers the note_schema trigger contract (parse/validate/match), rule-note
discovery (index-first with walk fallback, superseded exclusion, path
containment), and the hook injection contract on both surfaces: the
prompt-submit hook matches triggers against the prompt, the pre-tool-use
hook against the file path; non-matching rules are never injected, and
rules push even when parsight is down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import core.rule_triggers as rule_triggers  # noqa: E402
import note_schema  # noqa: E402
import pre_tool_use_hook  # noqa: E402
import user_prompt_submit_hook  # noqa: E402
from core import parsight_backend  # noqa: E402 -- ARC-006: patch where it lives


def _write_rule(
    vault: Path,
    stem: str = "sqlite-locking",
    triggers: str = "[sqlite]",
    type_: str = "rule",
    status: str | None = None,
    body: str = "Always enable WAL mode before long write transactions.",
) -> Path:
    """Write one rule note into *vault*; returns its path."""
    folder = vault / "Rules"
    folder.mkdir(parents=True, exist_ok=True)
    status_line = f"\nstatus: {status}" if status else ""
    note = (
        "---\n"
        "date: 2026-09-07\n"
        f"type: {type_}\n"
        "tags: [rule]\n"
        f"triggers: {triggers}\n"
        f'related: ["[[vault-index]]"]{status_line}\n'
        "---\n\n"
        f"# {stem.replace('-', ' ').title()}\n\n"
        f"{body}\n"
    )
    p = folder / f"{stem}.md"
    p.write_text(note, encoding="utf-8")
    return p


class TestTriggerContract:
    def test_parse_list_and_comma_string(self) -> None:
        assert note_schema.parse_triggers({"triggers": ["a b-c", "*.py"]}) == [
            "a b-c",
            "*.py",
        ]
        assert note_schema.parse_triggers({"triggers": "sqlite, *.ts"}) == [
            "sqlite",
            "*.ts",
        ]

    def test_validate_accepts_keywords_globs_always(self) -> None:
        assert note_schema.validate_triggers(["sqlite", "prompt-cache", "*.py"]) == []
        assert note_schema.validate_triggers(["always"]) == []

    def test_validate_rejects_bad_shapes(self) -> None:
        assert note_schema.validate_triggers([])  # empty is an error
        assert note_schema.validate_triggers("sqlite")  # not a list
        assert note_schema.validate_triggers(["Not A Keyword"])
        assert note_schema.validate_triggers(["always", "sqlite"])  # mixed
        assert note_schema.validate_triggers([42])

    def test_match_keyword_hyphen_or_space(self) -> None:
        assert note_schema.match_trigger(
            "prompt-cache", prompt="fix the prompt cache now"
        )
        assert note_schema.match_trigger(
            "prompt-cache", prompt="fix the prompt-cache now"
        )
        assert not note_schema.match_trigger("sqlite", prompt="totally unrelated words")

    def test_match_path_glob_and_word(self) -> None:
        assert note_schema.match_trigger("*.PY", path="/a/b/c.py")
        assert note_schema.match_trigger("sqlite", path="/x/y/sqlite_store.py")
        assert not note_schema.match_trigger("*.py", path="/a/b/c.ts")
        assert not note_schema.match_trigger("*.py", prompt="no path here")

    def test_always_matches_and_needs_no_context(self) -> None:
        assert note_schema.match_trigger("always", prompt="")
        assert note_schema.match_trigger("always", path="/any/file")


class TestLoadRuleNotes:
    def test_walk_fallback_filters_types_and_triggers(self, tmp_vault: Path) -> None:
        _write_rule(vault=tmp_vault, stem="alpha-rule")
        _write_rule(vault=tmp_vault, stem="beta-rule", triggers="[bench, *.rul]")
        _write_rule(vault=tmp_vault, stem="not-a-rule", type_="pattern")
        _write_rule(
            vault=tmp_vault,
            stem="retired-rule",
            status="superseded",
        )
        _write_rule(vault=tmp_vault, stem="no-trigger-rule", triggers="[]")
        rules = rule_triggers.load_rule_notes(tmp_vault)
        stems = {r["stem"] for r in rules}
        assert stems == {"alpha-rule", "beta-rule"}

    def test_index_path_revalidates_containment(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        p = _write_rule(vault=tmp_vault, stem="indexed-rule")
        outside = Path(tmp_vault) / ".." / "outside-rule.md"

        def fake_rows(
            vault: object = None, limit: int = 5000
        ) -> list[dict[str, object]]:
            return [
                {
                    "stem": "indexed-rule",
                    "path": str(p),
                    "folder": "Rules",
                    "title": "Indexed rule",
                    "summary": "body",
                    "tags": [],
                    "note_type": "rule",
                    "project": "",
                    "mtime": 1.0,
                },
                {
                    "stem": "outside-rule",
                    "path": str(outside),
                    "folder": "Rules",
                    "title": "Outside",
                    "summary": "",
                    "tags": [],
                    "note_type": "rule",
                    "project": "",
                    "mtime": 2.0,
                },
            ]

        monkeypatch.setattr(rule_triggers, "load_note_index_metadata", fake_rows)
        rules = rule_triggers.load_rule_notes(tmp_vault)
        assert [r["stem"] for r in rules] == ["indexed-rule"]

    def test_match_rules_attaches_matched(self) -> None:
        rules = [
            {"stem": "r1", "triggers": ["sqlite", "*.rul"], "title": "R1"},
        ]
        got = rule_triggers.match_rules(rules, prompt="sqlite trouble")
        assert got[0]["matched"] == ["sqlite"]
        assert rule_triggers.match_rules(rules, prompt="nothing here") == []


class TestPromptSubmitInjection:
    def test_rule_injects_despite_parsight_down(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_rule(vault=tmp_vault, stem="wal-rule")
        logs = tmp_vault / "logs"
        monkeypatch.setattr(user_prompt_submit_hook, "secure_log_dir", lambda: logs)
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda vault=None: False
        )
        monkeypatch.setattr(
            user_prompt_submit_hook, "write_hook_event", lambda *a, **k: None
        )
        result = user_prompt_submit_hook.run_recall(
            {"prompt": "hit an sqlite lock error again", "cwd": str(tmp_vault)}
        )
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "Vault rules — 1 rule(s) triggered:" in ctx
        assert "wal-rule" in ctx

    def test_non_matching_rule_never_injects(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_rule(vault=tmp_vault, stem="wal-rule")
        logs = tmp_vault / "logs"
        monkeypatch.setattr(user_prompt_submit_hook, "secure_log_dir", lambda: logs)
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda vault=None: False
        )
        result = user_prompt_submit_hook.run_recall(
            {"prompt": "quantum banana yodeling", "cwd": str(tmp_vault)}
        )
        assert result == {}

    def test_no_rules_and_parsight_down_still_empty(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        logs = tmp_vault / "logs"
        monkeypatch.setattr(user_prompt_submit_hook, "secure_log_dir", lambda: logs)
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda vault=None: False
        )
        assert (
            user_prompt_submit_hook.run_recall(
                {"prompt": "fix the sqlite thing", "cwd": str(tmp_vault)}
            )
            == {}
        )


class TestDoctorRule:
    def test_catalog_lists_the_rule(self) -> None:
        from doctor.protocol import RULE_SPECS

        assert any(s.name == "rule-triggers" for s in RULE_SPECS)

    def test_check_flags_missing_triggers(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        note = _write_rule(vault=tmp_vault, stem="bad-rule", triggers="[]")
        issues = check_note(note, {}, tmp_vault)
        assert any(i.code == "RULE_TRIGGERS" for i in issues)

    def test_check_flags_triggers_on_non_rule(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        note = _write_rule(
            vault=tmp_vault,
            stem="pattern-with-triggers",
            type_="pattern",
            triggers="[sqlite]",
        )
        issues = check_note(note, {}, tmp_vault)
        assert any(
            i.code == "RULE_TRIGGERS" and "not 'rule'" in i.message for i in issues
        )

    def test_check_clean_for_valid_rule(self, tmp_vault: Path) -> None:
        from doctor.check import check_note

        note = _write_rule(vault=tmp_vault, stem="good-rule")
        issues = check_note(note, {}, tmp_vault)
        assert not [i for i in issues if i.code == "RULE_TRIGGERS"]


class TestPreToolUseInjection:
    def _isolate(self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        logs = tmp_vault / "logs"
        monkeypatch.setattr(pre_tool_use_hook, "secure_log_dir", lambda: logs)
        monkeypatch.setattr(
            parsight_backend, "resolve_parsight_backend", lambda vault=None: False
        )
        monkeypatch.setattr(pre_tool_use_hook, "write_hook_event", lambda *a, **k: None)

    def test_path_glob_rule_injects(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._isolate(tmp_vault, monkeypatch)
        _write_rule(vault=tmp_vault, stem="rul-files", triggers='["*.rul"]')
        result = pre_tool_use_hook.run_injection(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/proj/thing.rul"},
                "cwd": str(tmp_vault),
            }
        )
        ctx = result["hookSpecificOutput"]["additionalContext"]
        assert "Vault rules — 1 rule(s) triggered:" in ctx
        assert "rul-files" in ctx

    def test_keyword_in_path_injects(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._isolate(tmp_vault, monkeypatch)
        _write_rule(vault=tmp_vault, stem="bench-rule", triggers="[bench]")
        result = pre_tool_use_hook.run_injection(
            {
                "tool_name": "Read",
                "tool_input": {"file_path": "/tmp/proj/bench.py"},
                "cwd": str(tmp_vault),
            }
        )
        assert "bench-rule" in result["hookSpecificOutput"]["additionalContext"]

    def test_non_matching_file_never_injects(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._isolate(tmp_vault, monkeypatch)
        _write_rule(vault=tmp_vault, stem="bench-rule", triggers="[bench]")
        assert (
            pre_tool_use_hook.run_injection(
                {
                    "tool_name": "Read",
                    "tool_input": {"file_path": "/tmp/proj/other.txt"},
                    "cwd": str(tmp_vault),
                }
            )
            == {}
        )

    def test_file_inside_vault_skips_rules_too(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._isolate(tmp_vault, monkeypatch)
        _write_rule(vault=tmp_vault, stem="bench-rule", triggers="[always]")
        assert (
            pre_tool_use_hook.run_injection(
                {
                    "tool_name": "Read",
                    "tool_input": {
                        "file_path": str(tmp_vault / "Patterns" / "note.md")
                    },
                    "cwd": str(tmp_vault),
                }
            )
            == {}
        )
