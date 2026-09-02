"""ENH-019: hook latency percentiles and the SessionStart budget alert.

Pins (QA-015 coverage for cli/stats/operations.py along the way):

* ``summarize_hook_latency`` over a 50-event fixture with two events over
  the registered 60 s SessionStart timeout: p50/p95/max ordering and
  ``timeouts == 2``.
* ``run_hooks`` prints the aggregate table followed by the raw events, and
  a fixture with SessionStart p95 of ~50000 ms produces the budget warning.
* ``vault-stats --health --json`` includes a ``hook_latency`` component.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cli.stats import operations as stats_ops  # noqa: E402
from vault_constants import HOOK_TIMEOUTS_MS  # noqa: E402


def _event(hook: str, duration_ms: int, ts: str | None = None) -> dict:
    from datetime import datetime

    if ts is None:
        ts = datetime.now().isoformat(timespec="seconds")
    return {"hook": hook, "ts": ts, "project": "p", "duration_ms": duration_ms}


class TestSummarizeHookLatency:
    def test_fifty_event_fixture_two_timeouts(self) -> None:
        # 48 SessionStart events ramping 100..4900 ms + 2 blowouts over 60 s.
        events = [_event("SessionStart", 100 + i * 100) for i in range(48)] + [
            _event("SessionStart", 61_000),
            _event("SessionStart", 90_000),
        ]
        aggregate = stats_ops.summarize_hook_latency(
            events, window_days=None, timeout_map=HOOK_TIMEOUTS_MS
        )
        agg = aggregate["SessionStart"]
        assert agg["count"] == 50
        assert agg["timeouts"] == 2
        assert agg["max_ms"] == 90_000
        assert 0 < agg["p50_ms"] < agg["p95_ms"] < agg["max_ms"]

    def test_async_hooks_never_count_timeouts(self) -> None:
        events = [_event("SessionEnd", 10_000_000)]
        aggregate = stats_ops.summarize_hook_latency(
            events, window_days=None, timeout_map=HOOK_TIMEOUTS_MS
        )
        assert aggregate["SessionEnd"]["timeouts"] == 0

    def test_window_filters_old_events(self) -> None:
        from datetime import datetime, timedelta

        old_ts = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
        events = [
            _event("SessionStart", 999_999, ts=old_ts),
            _event("SessionStart", 500),
        ]
        aggregate = stats_ops.summarize_hook_latency(events, window_days=7)
        assert aggregate["SessionStart"]["count"] == 1
        assert aggregate["SessionStart"]["max_ms"] == 500


class TestBudgetWarning:
    def test_p95_50000_ms_produces_warning(self) -> None:
        events = [_event("SessionStart", 40_000 + i * 600) for i in range(20)]
        aggregate = stats_ops.summarize_hook_latency(
            events, window_days=None, timeout_map=HOOK_TIMEOUTS_MS
        )
        assert aggregate["SessionStart"]["p95_ms"] >= 45_000
        warning = stats_ops.session_start_budget_warning(aggregate)
        assert warning is not None
        assert "SessionStart p95" in warning

    def test_healthy_latency_no_warning(self) -> None:
        events = [_event("SessionStart", 900) for _ in range(20)]
        aggregate = stats_ops.summarize_hook_latency(
            events, window_days=None, timeout_map=HOOK_TIMEOUTS_MS
        )
        assert stats_ops.session_start_budget_warning(aggregate) is None


class TestRunHooksOutput:
    @pytest.fixture()
    def vault_with_log(self, tmp_path: Path) -> Path:
        # 20 slow SessionStart events (p95 ~49 s) + a couple of fast ones.
        lines = [
            json.dumps(_event("SessionStart", 40_000 + i * 500)) for i in range(20)
        ] + [json.dumps(_event("SessionEnd", 120)) for _ in range(2)]
        (tmp_path / "hook_events.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
        return tmp_path

    def test_table_then_events_then_warning(
        self,
        vault_with_log: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Force a no-color rich console writing to stdout so the output is
        # plain assertable text.
        from rich.console import Console

        monkeypatch.setattr(
            stats_ops, "_get_console", lambda: Console(force_terminal=False)
        )
        stats_ops.run_hooks(20, vault=vault_with_log)
        out = capsys.readouterr().out
        assert "Hook Latency" in out
        assert "p95 ms" in out
        assert "timeouts" in out
        assert "Hook Events" in out
        assert out.index("Hook Latency") < out.index("Hook Events")
        assert "SessionStart p95" in out


class TestHealthComponent:
    def test_health_json_includes_hook_latency(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vault_health

        (tmp_path / "hook_events.log").write_text(
            json.dumps(_event("SessionStart", 5_000)) + "\n", encoding="utf-8"
        )
        report = vault_health.compute_health_report(tmp_path)
        names = [d.name for d in report.dimensions]
        assert "hook_latency" in names
        data = json.loads(vault_health.to_json(report))
        component = next(d for d in data["dimensions"] if d["name"] == "hook_latency")
        assert component["detail"]
