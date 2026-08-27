"""ENH-015: per-rule ``--only``/``--skip`` selection, ``--list-rules``, and
the end-of-run per-rule report for ``vault_doctor``.

Pins:

* ``--list-rules`` prints the whole catalog with a risk column and exits 0.
* ``--only``/``--skip`` gate the fix-mode dispatch AND the scan checks, so
  ``--fix-all --skip strip-prefixes`` performs no renames on a fixture that
  has a strippable prefix, and ``--fix-all --only tags`` runs nothing else.
* ``--only`` + ``--skip`` together exit 2 (argparse mutual exclusion).
* The per-rule table appears in ``--dry-run`` output.
* Registry/catalog consistency: every check Rule slug and every fix-mode
  flag has exactly one catalog row.
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

import doctor.cli as doctor_cli  # noqa: E402
import doctor.orchestrator as doctor_orch  # noqa: E402
import vault_doctor  # noqa: E402
from doctor.check import PRE_FM_RULES, RULES  # noqa: E402
from doctor.protocol import RULE_NAMES, RULE_SPECS  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A vault the doctor accepts (SEC-P001 vaults.yaml registration)."""
    v = tmp_path / "vault"
    v.mkdir()
    (v / "Daily").mkdir()
    (v / "Patterns").mkdir()
    _cfg_dir = tmp_path / ".config" / "parsidion"
    _cfg_dir.mkdir(parents=True, exist_ok=True)
    (_cfg_dir / "vaults.yaml").write_text(f"vaults:\n  test: {v}\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("CLAUDE_VAULT", str(v))
    return v


def _write_note(vault: Path, rel: str, body: str = "") -> Path:
    """Write a note with valid frontmatter unless *body* provides its own."""
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if not body.lstrip().startswith("---"):
        fm = (
            "---\n"
            "date: 2026-08-24\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[other-note]]"]\n'
            "---\n"
            "# Title\n"
        )
        body = fm + body
    p.write_text(body, encoding="utf-8")
    return p


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    """Invoke doctor.cli.main() with in-process state restored afterwards.

    main() swaps the process-wide ``vault_common.VAULT_ROOT`` and clears the
    ``resolve_vault``/``load_config`` caches, restoring them only via atexit
    (process end). Without this finally block the tmp-vault resolution stays
    cached and pollutes every later vault-resolution test in the session.
    """
    monkeypatch.setattr(sys, "argv", ["vault_doctor", *argv])
    saved_root = vault_doctor.vault_common.VAULT_ROOT
    try:
        doctor_cli.main()
    finally:
        vault_doctor.vault_common.VAULT_ROOT = saved_root
        vault_doctor.vault_common.clear_config_cache()
        vault_doctor.vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]


class _Spy:
    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self.calls = calls

    def __call__(self, *args: object, **kwargs: object) -> None:
        self.calls.append(self.name)


def _spy_all_modes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the five fix-mode runners in doctor.cli with recorders.

    Records the catalog rule name (not the function name) so assertions
    read in --only/--skip vocabulary.
    """
    calls: list[str] = []
    for name, rule in (
        ("run_fix_tags", "tags"),
        ("run_strip_prefixes", "strip-prefixes"),
        ("run_migrate_subfolders", "subfolder-prefix"),
        ("run_migrate_daily_notes", "daily-namespace"),
        ("run_fix_permissions", "permissions"),
    ):
        monkeypatch.setattr(doctor_cli, name, _Spy(rule, calls))
    return calls


# ---------------------------------------------------------------------------
# --list-rules
# ---------------------------------------------------------------------------


class TestListRules:
    def test_prints_every_rule_with_risk_column_and_exits_zero(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _run_cli(monkeypatch, ["--list-rules"])
        out = capsys.readouterr().out
        assert "risk" in out
        for spec in RULE_SPECS:
            assert spec.name in out, spec.name
        # main() returned normally (exit 0 when run as the CLI entry point).


# ---------------------------------------------------------------------------
# Selection semantics
# ---------------------------------------------------------------------------


class TestOnlySkipSelection:
    def test_only_and_skip_together_exit_2(self) -> None:
        with pytest.raises(SystemExit) as exc:
            doctor_cli._build_parser().parse_args(
                ["--only", "tags", "--skip", "headings"]
            )
        assert exc.value.code == 2

    def test_unknown_rule_name_rejected(self) -> None:
        with pytest.raises(SystemExit) as exc:
            doctor_cli._build_parser().parse_args(["--only", "no-such-rule"])
        assert exc.value.code == 2

    def test_only_tags_runs_only_the_tags_rule(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _spy_all_modes(monkeypatch)
        seen: dict[str, object] = {}

        def fake_scan(vault: Path, state: dict, notes: list, options: object) -> None:
            seen["options"] = options

        monkeypatch.setattr(doctor_cli, "run_scan_and_repair", fake_scan)
        _write_note(tmp_vault, "Patterns/other-note.md")
        _write_note(tmp_vault, "Patterns/some-note.md")

        _run_cli(monkeypatch, ["--fix-all", "--only", "tags"])

        assert calls == ["tags"]
        options = seen["options"]
        assert isinstance(options, vault_doctor.DoctorOptions)
        assert options.enabled_rules == frozenset({"tags"})
        # The AI repair stage is deselected too — not in --only tags.
        assert options.fix_frontmatter is False

    def test_skip_strip_prefixes_fix_all_performs_no_renames(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = _spy_all_modes(monkeypatch)
        # A note whose filename repeats its subfolder name — the exact
        # candidate run_strip_prefixes renames (Tools/cctmux/… → overview.md).
        victim = _write_note(tmp_vault, "Tools/cctmux/cctmux-overview.md")
        _write_note(tmp_vault, "Patterns/other-note.md")
        before = victim.read_text(encoding="utf-8")

        # Scan-and-repair runs for real; keep it AI-free and side-effect-free.
        monkeypatch.setattr(
            doctor_orch, "_apply_repairs_parallel", lambda batch, ctx: (0, 0, 0)
        )
        monkeypatch.setattr(
            doctor_orch,
            "_filter_clusters_with_claude",
            lambda clusters, **kw: clusters,
        )
        monkeypatch.setattr(doctor_orch, "_run_reindex", lambda *a, **kw: None)
        monkeypatch.setattr(
            vault_doctor.vault_common, "git_commit_vault", lambda *a, **kw: None
        )

        _run_cli(monkeypatch, ["--fix-all", "--skip", "strip-prefixes", "--execute"])

        # strip-prefixes deselected: runner not invoked, file not renamed.
        assert "strip-prefixes" not in calls
        assert victim.exists()
        assert victim.read_text(encoding="utf-8") == before
        # The rest of --fix-all still ran.
        for expected in ("tags", "subfolder-prefix", "daily-namespace", "permissions"):
            assert expected in calls

    def test_check_rules_filtered_by_selection(
        self,
        tmp_vault: Path,
    ) -> None:
        from doctor.links import build_note_map

        other = _write_note(tmp_vault, "Patterns/other-note.md")
        note = tmp_vault / "Patterns/broken.md"
        note.write_text(
            "---\n"
            "date: 20260824\n"  # invalid → INVALID_DATE (date-format rule)
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[does-not-exist]]"]\n'  # → BROKEN_WIKILINK
            "---\n"
            "# Title\n",
            encoding="utf-8",
        )
        note_map = build_note_map([other, note])

        all_issues = vault_doctor.check_note(note, note_map, tmp_vault)
        codes_all = {i.code for i in all_issues}
        assert "BROKEN_WIKILINK" in codes_all
        assert "INVALID_DATE" in codes_all

        only_links = vault_doctor.check_note(
            note, note_map, tmp_vault, frozenset({"broken-wikilinks"})
        )
        assert [i.code for i in only_links] == ["BROKEN_WIKILINK"]
        # Issues carry the rule slug for the report.
        assert {i.rule for i in only_links} == {"broken-wikilinks"}


# ---------------------------------------------------------------------------
# Per-rule report
# ---------------------------------------------------------------------------


class TestRuleReport:
    def test_table_appears_in_dry_run_output(
        self,
        tmp_vault: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        note = tmp_vault / "Patterns/broken.md"
        note.write_text(
            "---\n"
            "date: 2026-08-24\n"
            "type: pattern\n"
            "confidence: high\n"
            'related: ["[[does-not-exist]]"]\n'
            "---\n"
            "# Broken\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(vault_doctor, "_run_reindex", lambda *a, **kw: None)

        _run_cli(monkeypatch, ["--dry-run"])

        out = capsys.readouterr().out
        assert "Rule report:" in out
        assert "broken-wikilinks" in out
        assert "found | fixed | skipped" in out


# ---------------------------------------------------------------------------
# Registry / catalog consistency
# ---------------------------------------------------------------------------


class TestCatalogConsistency:
    def test_every_registry_rule_has_a_catalog_row(self) -> None:
        check_targets = {spec.target for spec in RULE_SPECS if spec.kind == "check"}
        for rule in (*PRE_FM_RULES, *RULES):
            assert rule.slug in RULE_NAMES, rule.name
            assert rule.name in check_targets, rule.name

    def test_every_catalog_check_has_a_registry_rule(self) -> None:
        registry_names = {r.name for r in (*PRE_FM_RULES, *RULES)}
        for spec in RULE_SPECS:
            if spec.kind == "check":
                assert spec.target in registry_names, spec.name

    def test_bulk_rules_are_the_volume_operations(self) -> None:
        bulk = {spec.name for spec in RULE_SPECS if spec.risk == "bulk"}
        assert bulk == {
            "frontmatter-repair",
            "tags",
            "strip-prefixes",
            "subfolder-prefix",
            "daily-namespace",
        }
