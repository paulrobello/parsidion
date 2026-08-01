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
            dry_run=True,
            fix_frontmatter=True,  # even with fix enabled…
            fix_sessions=False,
            errors_only=False,
            no_state=True,
            model=None,
            limit=0,
            jobs=1,
            timeout=10,
            fix_headings=True,
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
            dry_run=False,
            fix_frontmatter=True,
            fix_sessions=False,
            errors_only=False,
            no_state=True,
            model=None,
            limit=0,
            jobs=1,
            timeout=10,
            fix_headings=True,
        )

        after = bad.read_text(encoding="utf-8")
        # Heading was promoted; the body line is intact.
        assert "# Should Be H1" in after
        assert "## Should Be H1" not in after
        # State was recorded for the repaired note.
        assert any("fixed" == v.get("status") for v in state.get("notes", {}).values())

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
            dry_run=False,
            fix_frontmatter=True,
            fix_sessions=False,
            errors_only=False,
            no_state=True,
            model=None,
            limit=0,
            jobs=1,
            timeout=10,
            fix_headings=True,
        )

        after = target.read_text(encoding="utf-8")
        assert f"[[{stem}]]" not in after
        assert "[[other-note]]" in after  # the non-self link is preserved


# ---------------------------------------------------------------------------
# run_scan_and_repair — manual-only classification marks state as skipped
# ---------------------------------------------------------------------------


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
            dry_run=False,
            fix_frontmatter=True,
            fix_sessions=False,
            errors_only=False,
            no_state=True,
            model=None,
            limit=0,
            jobs=1,
            timeout=10,
            fix_headings=True,
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
                dry_run=False,
                fix_frontmatter=False,
                fix_sessions=True,
                errors_only=False,
                no_state=True,
                model=None,
                limit=0,
                jobs=1,
                timeout=10,
                fix_headings=True,
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
