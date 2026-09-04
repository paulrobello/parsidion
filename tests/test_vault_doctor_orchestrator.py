"""ARC-008 / QA-003: tests for the decomposed ``doctor`` package.

These tests pin the behavior of:

* ``run_scan_and_repair`` — the orchestrator the original audit flagged as
  CC-58 with zero coverage on lines 2587-3109.
* The CLI ``--fix-all`` dispatch (registry-driven as of ARC-008).
* Per-destructive-mode dry-run vs ``--execute`` semantics.

Behavior is what's pinned — these tests are agnostic about which submodule
the function lives in, so future reshuffles inside ``doctor/`` won't churn
them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the scripts dir importable.
SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_doctor  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault with the minimal structure doctor expects."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Daily").mkdir()
    (v / "Patterns").mkdir()
    (v / "Debugging").mkdir()
    # SEC-P001: register v in a test-local vaults.yaml so any internal
    # resolve_vault() call inside the doctor accepts it.
    _cfg_dir = tmp_path / ".config" / "parsidion"
    _cfg_dir.mkdir(parents=True, exist_ok=True)
    (_cfg_dir / "vaults.yaml").write_text(f"vaults:\n  test: {v}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    return v


@pytest.fixture()
def patch_vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point vault_doctor at the temp vault (mirrors the existing test fixtures)."""
    monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
    # SEC-P001: also point CLAUDE_VAULT at the temp vault so resolve_vault()
    # inside doctor subroutines returns the test vault.
    monkeypatch.setenv("CLAUDE_VAULT", str(tmp_vault))


def _write_note(
    vault: Path, rel: str, body: str = "", *, stem: str | None = None
) -> Path:
    """Write a note under ``vault`` and return its path.

    If ``body`` doesn't start with ``---``, a minimal valid frontmatter block
    is prepended so the note passes the doctor's MISSING_FRONTMATTER check.
    """
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if not body.lstrip().startswith("---"):
        fm = (
            "---\n"
            "date: 2026-07-28\n"
            "type: pattern\n"
            "tags: [test]\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "# Heading\n"
            "body\n"
        )
        body = fm + body
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# run_scan_and_repair — dry-run makes no filesystem change
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
class TestScanDryRunIsReadOnly:
    """ARC-008 acceptance: dry-run must not touch the filesystem."""

    def test_dry_run_leaves_notes_unchanged(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Plant a note with a BROKEN_WIKILINK + ORPHAN_NOTE so the scanner has
        # something to report — but dry-run must not write a fix.
        other = _write_note(tmp_vault, "Patterns/other-note.md")
        broken = tmp_vault / "Patterns/broken.md"
        broken.write_text(
            "---\n"
            "date: 2026-07-28\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[does-not-exist]]"]\n'
            "---\n"
            "# Broken\n"
            "body\n",
            encoding="utf-8",
        )
        broken_before = broken.read_text(encoding="utf-8")
        other_before = other.read_text(encoding="utf-8")

        state = {"last_run": None, "notes": {}}
        # Patch out the AI backend so a stray fix_frontmatter=True could not
        # call out even if the function mistook dry_run for execute.
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )

        vault_doctor.run_scan_and_repair(
            tmp_vault,
            state,
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=True,
                errors_only=False,
                fix_frontmatter=True,  # even with fix enabled…
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        # No filesystem change: the two notes' bytes are identical.
        assert broken.read_text(encoding="utf-8") == broken_before
        assert other.read_text(encoding="utf-8") == other_before
        # And no state file was written (dry-run must skip save_state).
        assert not (tmp_vault / "doctor_state.json").exists()
        # Scanner reported the broken link.
        out = capsys.readouterr().out
        assert "BROKEN_WIKILINK" in out or "does-not-exist" in out


# ---------------------------------------------------------------------------
# run_scan_and_repair — execute path produces expected content
# ---------------------------------------------------------------------------


# QA-001: tests below drive the real run_scan_and_repair end to end —
# multi-second work whose wall-clock time balloons under coverage
# instrumentation and machine load.  The default 10 s per-test timeout
# (pyproject.toml) trips them nondeterministically in full-suite runs, so
# each run_scan_and_repair-driving class raises its ceiling to 60 s.
@pytest.mark.timeout(60)
class TestScanExecuteAppliesPythonOnlyFixes:
    """The doctor's Python-only fixers (heading promotion, self-ref removal)
    must fire on execute even when the AI backend is unavailable — they don't
    need Claude.  This exercises the _repair_one dispatch + the orchestrator's
    repair loop end-to-end."""

    def test_heading_promotion_fires_on_execute(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Note with a ## heading where # should be (HEADING_MISMATCH).
        # The AI backend is mocked to return None so we know the fix came from
        # the Python-only _auto_fix_headings path inside _repair_one.
        _write_note(tmp_vault, "Patterns/other-note.md")
        bad = tmp_vault / "Patterns/bad-heading.md"
        bad_body = (
            "---\n"
            "date: 2026-07-28\n"
            "type: pattern\n"
            "tags: [test]\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "## Should Be H1\n"
            "body\n"
        )
        bad.write_text(bad_body, encoding="utf-8")
        # Patch the module-level _vault_path so _rel(note_path) inside
        # _repair_one resolves against the temp vault (mirrors the existing
        # test_vault_doctor.py / test_vault_doctor_fixes.py pattern).
        monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )
        # Avoid the reindex subprocess (we don't have update_index.py available
        # in this test sandbox).
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)

        state = {"last_run": None, "notes": {}}
        vault_doctor.run_scan_and_repair(
            tmp_vault,
            state,
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        after = bad.read_text(encoding="utf-8")
        # Heading was promoted; the body line is intact.
        assert "# Should Be H1" in after
        assert "## Should Be H1" not in after
        # State was recorded for the repaired note.
        assert any("fixed" == v.get("status") for v in state.get("notes", {}).values())

    def test_repairs_are_committed_by_the_repair_phase(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The repair phase must commit its own notes.

        The reindex that follows stages only CLAUDE.md/TAGS.md/MANIFEST.md, so
        without this the AI's edits sat dirty until an unrelated later hook
        swept them into a 'chore(vault): session notes' commit — model-authored
        changes attributed to a commit that never mentions them.
        """
        _write_note(tmp_vault, "Patterns/other-note.md")
        bad = tmp_vault / "Patterns/bad-heading.md"
        bad.write_text(
            "---\n"
            "date: 2026-07-28\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "## Should Be H1\n"
            "body\n",
            encoding="utf-8",
        )
        messages: list[str] = []
        monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        monkeypatch.setattr(
            vault_doctor.vault_common,
            "git_commit_vault",
            lambda msg, **kw: messages.append(msg),
        )

        vault_doctor.run_scan_and_repair(
            tmp_vault,
            {"last_run": None, "notes": {}},
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        repair_commits = [m for m in messages if "repair frontmatter" in m]
        assert repair_commits, f"no repair commit was made; got {messages}"
        assert "vault_doctor" in repair_commits[0]

    def test_fix_all_never_substitutes_a_daily_note_for_a_broken_link(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full pipeline guard for the 2026-08-10 regression.

        A vault whose only plausible semantic match for a broken link is a daily
        note must come out with the link dropped, not rewritten to
        ``[[NN-probello]]``.
        """
        import json as _json

        (tmp_vault / "Daily" / "2026-08").mkdir(parents=True, exist_ok=True)
        daily = tmp_vault / "Daily/2026-08/10-probello.md"
        daily.write_text(
            "---\ndate: 2026-08-10\ntype: daily\ntags: [daily]\nrelated: []\n---\n\n"
            "## Sessions\n\n### Session: par-rt-db\n",
            encoding="utf-8",
        )
        _write_note(tmp_vault, "Patterns/keeper.md")
        subject = tmp_vault / "Patterns/subject.md"
        subject.write_text(
            "---\n"
            "date: 2026-08-10\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[par-rt-db]]", "[[keeper]]"]\n'
            "---\n\n# Subject\n\nBody.\n",
            encoding="utf-8",
        )

        # vault-search only ever offers the daily note.
        class _Completed:
            returncode = 0
            stdout = _json.dumps(
                [{"path": str(daily), "stem": daily.stem, "score": 0.95}]
            )

        monkeypatch.setattr(
            vault_doctor.subprocess, "run", lambda *a, **kw: _Completed()
        )
        monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        monkeypatch.setattr(
            vault_doctor.vault_common, "git_commit_vault", lambda *a, **kw: None
        )

        vault_doctor.run_scan_and_repair(
            tmp_vault,
            {"last_run": None, "notes": {}},
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        after = subject.read_text(encoding="utf-8")
        assert "-probello" not in after, f"daily note was substituted in:\n{after}"
        assert "[[keeper]]" in after

    def test_self_ref_removed_on_execute(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Note whose `related` field cites itself (SELF_REF).
        _write_note(tmp_vault, "Patterns/other-note.md")
        stem = "self-referential"
        target = tmp_vault / "Patterns" / f"{stem}.md"
        target.write_text(
            "---\n"
            "date: 2026-07-28\n"
            "type: pattern\n"
            "confidence: high\n"
            f'related: ["[[{stem}]]", "[[other-note]]"]\n'
            "---\n"
            "# Self\n"
            "body\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
        monkeypatch.setattr(
            vault_doctor.ai_backend, "run_ai_prompt", lambda *a, **kw: None
        )
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)

        state = {"last_run": None, "notes": {}}
        vault_doctor.run_scan_and_repair(
            tmp_vault,
            state,
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        after = target.read_text(encoding="utf-8")
        assert f"[[{stem}]]" not in after
        assert "[[other-note]]" in after  # the non-self link is preserved


# ---------------------------------------------------------------------------
# run_scan_and_repair — manual-only classification marks state as skipped
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
class TestScanManualOnlySkipsState:
    """A note whose only issue is non-repairable (FLAT_DAILY) must be marked
    ``skipped`` in state and not retried on every run."""

    def test_flat_daily_is_skipped(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        flat = tmp_vault / "Daily" / "2026-07-28.md"
        flat.parent.mkdir(parents=True, exist_ok=True)
        flat.write_text(
            "---\ndate: 2026-07-28\ntype: daily\n---\n# Today\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        state = {"last_run": None, "notes": {}}
        vault_doctor.run_scan_and_repair(
            tmp_vault,
            state,
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )

        # The flat daily note's relative path is the state key.
        key = "Daily/2026-07-28.md"
        assert key in state.get("notes", {})
        assert state["notes"][key]["status"] == "skipped"
        # And the file on disk was not modified.
        assert "2026-07-28" in flat.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# run_scan_and_repair — fix_sessions early-exit
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
class TestScanFixSessionsExits:
    """``fix_sessions=True`` must print the session-duplicate report and then
    ``sys.exit(0)`` so the rest of the scan pipeline doesn't run."""

    def test_fix_sessions_exits_zero(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        state = {"last_run": None, "notes": {}}
        with pytest.raises(SystemExit) as exc:
            vault_doctor.run_scan_and_repair(
                tmp_vault,
                state,
                notes=[],
                options=vault_doctor.DoctorOptions(
                    dry_run=False,
                    errors_only=False,
                    fix_frontmatter=False,
                    fix_headings=True,
                    fix_sessions=True,
                    jobs=1,
                    limit=0,
                    model=None,
                    no_state=True,
                    timeout=10,
                ),
            )
        assert exc.value.code == 0
        # The session report (or "No duplicate session IDs found.") printed.
        assert capsys.readouterr().out  # something was emitted


# ---------------------------------------------------------------------------
# Per-mode destructive tests — dry-run is read-only, execute writes
# ---------------------------------------------------------------------------


class TestStripPrefixesRoundTrip:
    """--strip-prefixes dry-run leaves files in place; --execute renames."""

    def _setup(self, vault: Path) -> tuple[Path, Path]:
        sub = vault / "Projects" / "cctmux"
        sub.mkdir(parents=True)
        old = sub / "cctmux-overview.md"
        old.write_text(
            "---\n"
            "date: 2026-07-28\n"
            "type: project\n"
            "confidence: high\n"
            'related: ["[[cctmux-overview]]"]\n'
            "---\n"
            "# cctmux overview\n",
            encoding="utf-8",
        )
        return sub, old

    def test_dry_run_makes_no_change(self, tmp_vault: Path) -> None:
        sub, old = self._setup(tmp_vault)
        vault_doctor.run_strip_prefixes(dry_run=True, vault_path=tmp_vault)
        assert old.exists()
        assert not (sub / "overview.md").exists()

    def test_execute_renames(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sub, old = self._setup(tmp_vault)
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        vault_doctor.run_strip_prefixes(dry_run=False, vault_path=tmp_vault)
        assert not old.exists()
        assert (sub / "overview.md").exists()


class TestFixPermissionsDryVsExecute:
    """--fix-permissions dry-run prints but doesn't chmod; --execute chmods."""

    def test_dry_run_does_not_chmod(
        self, tmp_vault: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        secret = tmp_vault / "pending_summaries.jsonl"
        secret.write_text("[]", encoding="utf-8")
        import os

        os.chmod(secret, 0o644)
        before = secret.stat().st_mode & 0o777

        repaired = vault_doctor.run_fix_permissions(tmp_vault, dry_run=True)
        out = capsys.readouterr().out
        assert "would chmod" in out
        assert repaired == 0
        # File mode unchanged.
        assert secret.stat().st_mode & 0o777 == before

    def test_execute_chmods(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = tmp_vault / "dead_letters.jsonl"
        secret.write_text("[]", encoding="utf-8")
        import os

        os.chmod(secret, 0o644)
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)

        repaired = vault_doctor.run_fix_permissions(tmp_vault, dry_run=False)
        assert repaired >= 1
        assert secret.stat().st_mode & 0o777 == 0o600


# ---------------------------------------------------------------------------
# Registry shape — adding a fix-mode is one tuple, not two if-branches
# ---------------------------------------------------------------------------


class TestFixModeRegistry:
    """The --fix-all dispatch is now data-driven via doctor.protocol.FixMode.
    A new mode is one tuple append + its argparse declaration; the dispatch
    loop and the --fix-all implication stay in lockstep because they read the
    same registry."""

    def test_fix_mode_is_frozen_dataclass(self) -> None:
        from dataclasses import FrozenInstanceError

        from doctor.protocol import FixMode

        m = FixMode("fix_x", lambda v, d: None, "label")
        # Frozen: callers can't mutate the registry in place.
        with pytest.raises(FrozenInstanceError):
            m.flag = "fix_y"  # type: ignore[misc]

    def test_run_fix_modes_dispatches_selected(self, tmp_vault: Path) -> None:
        from doctor.protocol import FixMode, run_fix_modes

        called: list[str] = []

        def runner(vault: Path, dry: bool) -> None:
            called.append(f"{vault.name}:{dry}")

        modes = (
            FixMode("fix_a", runner, "a"),
            FixMode("fix_b", runner, "b"),
        )

        class Args:
            fix_all = False
            execute = True
            fix_a = True
            fix_b = False

        standalone_ran = run_fix_modes(modes, Args(), tmp_vault)
        # Standalone (non-fix-all) mode runs exactly once then signals return.
        assert standalone_ran is True
        assert called == [f"{tmp_vault.name}:False"]

    def test_run_fix_modes_fix_all_runs_every_selected(self, tmp_vault: Path) -> None:
        from doctor.protocol import FixMode, run_fix_modes

        called: list[str] = []

        def runner(vault: Path, dry: bool) -> None:
            called.append(vault.name)

        modes = tuple(FixMode(f"fix_{n}", runner, str(n)) for n in ("a", "b", "c"))

        class Args:
            fix_all = True
            execute = True
            fix_a = True
            fix_b = True
            fix_c = False  # unselected — must not run

        standalone_ran = run_fix_modes(modes, Args(), tmp_vault)
        # fix_all runs every selected mode and signals continue.
        assert standalone_ran is False
        # fix_a + fix_b ran, fix_c did not.
        assert called == [tmp_vault.name, tmp_vault.name]


@pytest.mark.timeout(60)
class TestDeterministicFrontmatterPrePass:
    """The two detection-only codes with a safe mechanical fix
    (NESTED_FM_KEY ``metadata:`` wrapper, SCALAR_LIST_FIELD) are repaired by a
    deterministic pre-pass — no AI backend call, even with fix_frontmatter=True.
    """

    def _run(self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
        monkeypatch.setattr(vault_doctor, "_vault_path", tmp_vault)
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)
        state: dict = {"last_run": None, "notes": {}}
        vault_doctor.run_scan_and_repair(
            tmp_vault,
            state,
            notes=[],
            options=vault_doctor.DoctorOptions(
                dry_run=False,
                errors_only=False,
                fix_frontmatter=True,
                fix_headings=True,
                fix_sessions=False,
                jobs=1,
                limit=0,
                model=None,
                no_state=True,
                timeout=10,
            ),
        )
        return state

    def test_metadata_wrapper_fixed_without_ai(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_note(tmp_vault, "Patterns/other-note.md")
        bad = tmp_vault / "Patterns/wrapped.md"
        bad.write_text(
            "---\n"
            "date: 2026-08-11\n"
            "metadata:\n"
            "  type: pattern\n"
            "  confidence: high\n"
            "  tags: [rust]\n"
            '  related: ["[[other-note]]"]\n'
            "---\n"
            "# Heading\n",
            encoding="utf-8",
        )
        calls: list[int] = []
        monkeypatch.setattr(
            vault_doctor.ai_backend,
            "run_ai_prompt",
            lambda *a, **kw: calls.append(1) or None,
        )
        state = self._run(tmp_vault, monkeypatch)
        after = bad.read_text(encoding="utf-8")
        assert "metadata:" not in after
        assert "\ntype: pattern\n" in after
        # No AI call: the deterministic pre-pass cleared the only issues.
        assert calls == []
        rel = str(bad.relative_to(tmp_vault))
        assert state["notes"][rel]["status"] == "fixed"

    def test_scalar_list_fixed_without_ai(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_note(tmp_vault, "Patterns/other-note.md")
        bad = tmp_vault / "Patterns/scalar.md"
        bad.write_text(
            "---\n"
            "date: 2026-08-11\n"
            "type: pattern\n"
            "confidence: high\n"
            "tags: rust serde\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "# Heading\n",
            encoding="utf-8",
        )
        calls: list[int] = []
        monkeypatch.setattr(
            vault_doctor.ai_backend,
            "run_ai_prompt",
            lambda *a, **kw: calls.append(1) or None,
        )
        state = self._run(tmp_vault, monkeypatch)
        after = bad.read_text(encoding="utf-8")
        assert "tags: [rust, serde]" in after
        assert calls == []
        rel = str(bad.relative_to(tmp_vault))
        assert state["notes"][rel]["status"] == "fixed"

    def test_stray_list_items_fixed_without_ai(
        self, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_note(tmp_vault, "Patterns/other-note.md")
        _write_note(tmp_vault, "Patterns/extra-note.md")
        bad = tmp_vault / "Patterns/stray.md"
        bad.write_text(
            "---\n"
            "date: 2026-08-11\n"
            "type: pattern\n"
            "tags: [test]\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            '  - "[[extra-note]]"\n'
            '  - "stray-tag"\n'
            "---\n"
            "# Heading\n",
            encoding="utf-8",
        )
        calls: list[int] = []
        monkeypatch.setattr(
            vault_doctor.ai_backend,
            "run_ai_prompt",
            lambda *a, **kw: calls.append(1) or None,
        )
        state = self._run(tmp_vault, monkeypatch)
        after = bad.read_text(encoding="utf-8")
        assert 'related: ["[[other-note]]", "[[extra-note]]"]' in after
        assert "stray-tag" not in after
        assert calls == []
        rel = str(bad.relative_to(tmp_vault))
        assert state["notes"][rel]["status"] == "fixed"
