#!/usr/bin/env python3
"""SessionStart hook latency benchmark and budget gate (ENH-023).

For each vault size, generates a synthetic vault (``gen_bench_vault.py``),
invokes ``session_start_hook.py`` R times via subprocess exactly as Claude
Code would (JSON on stdin, fresh process per rep), and reports wall-time
medians with the per-stage breakdown the hook now writes into its hook event
(``stages_ms``). Fails when a size's median exceeds its budget — the
pre-merge/pre-release gate for hook-latency work.

On-demand only (``make bench-hooks``); deliberately NOT part of
``make checkall``/CI because absolute budgets are machine-dependent.

Stage timings and the hook's own accounting are read from the LAST line of
``<vault>/hook_events.log`` after each rep — the same production path
``vault-stats --hooks`` reads. Semantic search, parsight, and AI selection are
pinned off in the bench vault's config so the numbers measure this repo's
code.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "parsidion" / "scripts"
SESSION_START_HOOK = SCRIPTS_DIR / "session_start_hook.py"
GEN_BENCH_VAULT = Path(__file__).resolve().parent / "gen_bench_vault.py"
RESULTS_FILE = Path(__file__).resolve().parent / "results.jsonl"
_HOOK_TIMEOUT_S = 120

# Median wall-time budgets per vault size, milliseconds. The budget covers the
# whole hook process (interpreter + imports + all stages) — that is the latency
# a session start actually pays.
# Calibrated 2026-08-27 on macOS-26.6.2-arm64 (Apple silicon) / Python 3.14:
# first harness run observed N=500 median ~0.16 s, N=5000 median ~0.34 s.
# Budgets sit well above the observed medians so normal machine noise passes
# and real regressions (subprocess towers, full-table scans, O(n) assembly)
# fail. Recalibrate deliberately when the hook's workload changes, never to
# make a regression pass.
_BUDGET_MS: dict[int, float] = {500: 1000.0, 5000: 2500.0}
_DEFAULT_SIZES = (500, 5000)
_DEFAULT_REPS = 5
_BENCH_PROJECT = "bench"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SessionStart latency bench + budget gate (ENH-023)."
    )
    parser.add_argument(
        "--sizes",
        type=str,
        default=",".join(str(s) for s in _DEFAULT_SIZES),
        help=f"comma-separated vault sizes (default: {','.join(str(s) for s in _DEFAULT_SIZES)})",
    )
    parser.add_argument(
        "--reps",
        type=int,
        default=_DEFAULT_REPS,
        help=f"repetitions per size, median is gated (default: {_DEFAULT_REPS})",
    )
    parser.add_argument(
        "--slow-ms",
        type=float,
        default=0.0,
        help=(
            "inject this much artificial delay into each measured rep "
            "(gate self-test: a value above the budget must flip the exit code)"
        ),
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the generated vaults and print their paths (debugging)",
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="directory for generated vaults (default: a fresh temp dir; "
        "an existing vault-<N> inside it is reused, skipping regeneration)",
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=RESULTS_FILE,
        help=f"JSONL trend file to append (default: {RESULTS_FILE})",
    )
    return parser.parse_args()


def _run_hook(
    vault: Path, cwd: Path, slow_ms: float
) -> tuple[float, dict[str, object]]:
    """One rep: run the hook as a fresh process, return (wall_ms, event)."""
    # SEC-P001: the resolver allowlists named vaults only; the generator
    # registered each vault in <vault_root>/.config/parsidion/vaults.yaml, so
    # expose that registry and CLAUDE_VAULT to the hook subprocess.
    xdg_config = vault.parent / ".config"
    env = {
        **os.environ,
        "CLAUDE_VAULT": str(vault),
        "XDG_CONFIG_HOME": str(xdg_config),
    }
    # PARSIDION_INTERNAL would make the hook print {} and skip all work.
    env.pop("PARSIDION_INTERNAL", None)
    payload = json.dumps({"cwd": str(cwd)})
    start = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(SESSION_START_HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd),
        timeout=_HOOK_TIMEOUT_S,
    )
    if slow_ms:
        # Inside the measured window on purpose: this IS the artificial
        # slowdown the gate must catch.
        time.sleep(slow_ms / 1000.0)
    wall_ms = (time.perf_counter() - start) * 1000.0
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"hook exited {proc.returncode} against {vault}")
    try:
        out = json.loads(proc.stdout)
        assert "hookSpecificOutput" in out
    except (json.JSONDecodeError, AssertionError):
        raise SystemExit(
            f"hook produced no hookSpecificOutput against {vault}"
        ) from None

    event = _last_hook_event(vault)
    if not event or event.get("hook") != "SessionStart":
        raise SystemExit(f"no SessionStart event in {vault / 'hook_events.log'}")
    return wall_ms, event


def _last_hook_event(vault: Path) -> dict[str, object] | None:
    log = vault / "hook_events.log"
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if line:
            try:
                return json.loads(line)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                continue
    return None


def _stage_medians(events: list[dict[str, object]]) -> dict[str, float]:
    stages: dict[str, list[float]] = {}
    for event in events:
        per_stage = event.get("stages_ms")
        if isinstance(per_stage, dict):
            for name, value in per_stage.items():
                if isinstance(value, (int, float)):
                    stages.setdefault(str(name), []).append(float(value))
    return {name: round(statistics.median(vals), 1) for name, vals in stages.items()}


def _git_commit() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.stdout.strip() if proc.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> None:
    args = _parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    for size in sizes:
        if size not in _BUDGET_MS:
            raise SystemExit(
                f"no budget defined for size {size}; known: {sorted(_BUDGET_MS)}"
            )

    tmp_root: Path | None = None
    if args.vault_root is not None:
        vault_root = args.vault_root
        vault_root.mkdir(parents=True, exist_ok=True)
    else:
        tmp_root = Path(tempfile.mkdtemp(prefix="parsidion-bench-"))
        vault_root = tmp_root

    cwd_dir = vault_root / _BENCH_PROJECT
    cwd_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    runs: list[dict[str, object]] = []
    try:
        for size in sizes:
            vault = vault_root / f"vault-{size}"
            if not (vault / "CLAUDE.md").exists():
                gen = subprocess.run(
                    [
                        sys.executable,
                        str(GEN_BENCH_VAULT),
                        "--notes",
                        str(size),
                        "--out",
                        str(vault),
                        "--project",
                        _BENCH_PROJECT,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
                if gen.returncode != 0:
                    sys.stderr.write(gen.stderr)
                    raise SystemExit(f"vault generation failed for N={size}")

            walls_ms: list[float] = []
            events: list[dict[str, object]] = []
            for _ in range(args.reps):
                wall_ms, event = _run_hook(vault, cwd_dir, args.slow_ms)
                walls_ms.append(wall_ms)
                events.append(event)

            median_ms = statistics.median(walls_ms)
            budget_ms = _BUDGET_MS[size]
            passed = median_ms <= budget_ms
            stage_p50 = _stage_medians(events)
            notes_injected = events[-1].get("notes_injected")

            verdict = "PASS" if passed else "FAIL"
            print(
                f"\nN={size}  {verdict}  median {median_ms:8.1f} ms  "
                f"(budget {budget_ms:.0f} ms, reps={args.reps}, "
                f"notes_injected={notes_injected})"
            )
            print("  per-stage p50 (hook's own accounting):")
            for name in sorted(stage_p50):
                print(f"    {name:<14} {stage_p50[name]:8.1f} ms")
            hook_reported = events[-1].get("duration_ms")
            if isinstance(hook_reported, (int, float)):
                overhead = median_ms - float(hook_reported)
                print(
                    f"    {'(process+io)':<14} {overhead:8.1f} ms  "
                    "(wall minus hook-reported)"
                )

            if not passed:
                failures.append(
                    f"N={size}: median {median_ms:.0f} ms > {budget_ms:.0f} ms"
                )
            runs.append(
                {
                    "size": size,
                    "reps_ms": [round(w, 1) for w in walls_ms],
                    "median_ms": round(median_ms, 1),
                    "budget_ms": budget_ms,
                    "pass": passed,
                    "stage_ms_p50": stage_p50,
                    "notes_injected": notes_injected,
                    "slow_ms": args.slow_ms,
                }
            )

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "machine": platform.platform(),
            "python": platform.python_version(),
            "git": _git_commit(),
            "runs": runs,
        }
        args.results.parent.mkdir(parents=True, exist_ok=True)
        with open(args.results, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        print(f"\nresults appended to {args.results}")
    finally:
        if args.keep:
            print(f"vaults kept under {vault_root}")
        elif tmp_root is not None:
            shutil.rmtree(tmp_root, ignore_errors=True)

    if failures:
        print("\nBUDGET BREACH:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
