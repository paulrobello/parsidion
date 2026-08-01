#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""ENH-008 Step 5 — prompt evaluation driver.

Scores the six externalized prompts against the golden case set under
``tests/fixtures/prompts/golden/<prompt>/``, so a prompt edit can be evaluated
rather than guessed at. Extends the ``embed_eval_*`` harness conventions (PEP
723 script, Rich table, JSON result file) rather than building a parallel one.

The per-prompt logic (how to render, parse, and score each prompt's output)
lives in :mod:`tools.eval.evaluators` — one module per prompt, because each has
a different variable contract, output shape, and rubric. This driver is the
thin AI-call + cost-control + caching + display layer that dispatches over
``evaluators.EVALUATORS``.

Each golden case renders the prompt through the real ``prompt_templates`` loader
(the same path production uses), calls the configured AI backend, and scores the
output against the case's ``expected.yaml``. The render/parse/score logic is
unit-tested without an AI call; only this driver's opt-in ``--yes`` run bills.

Cost control (the plan's hard requirement):

- Defaults to the small model tier.
- Prints the projected AI call count before starting and asks for confirmation
  above a threshold (override with ``--yes``).
- Caches each case's result keyed by ``(prompt_id, version, model, case_id)``
  so re-running after editing one case does not re-bill the rest.

This script is OPT-IN — ``make checkall`` does not invoke it. Run it by hand:

    # Score the summarizer prompt against its golden set:
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session

    # Score another prompt (each has its own golden subdir + rubric):
    uv run tools/eval/prompt_eval_run.py --prompt summarize-chunk --limit 3

    # Use a larger model (explicit opt-in for higher cost):
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session --model claude-sonnet-4-5
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Literal

from rich.console import Console  # type: ignore[import-untyped]
from rich.prompt import Confirm  # type: ignore[import-untyped]
from rich.table import Table  # type: ignore[import-untyped]

# Make the skills scripts importable (vault_common, ai_backend, prompt_templates,
# note_schema) and the sibling evaluators package — matches embed_eval_common.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from evaluators import EVALUATORS, ScoredCase  # noqa: E402

_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "parsidion"
    / "prompt-eval"
)

console = Console()

_CONFIRM_THRESHOLD = 12  # confirm before running more than this many uncached calls


# ---------------------------------------------------------------------------
# AI call
# ---------------------------------------------------------------------------


def _call_ai(prompt: str, model: str | None, model_tier: str) -> str | None:
    """Invoke the configured AI backend (same path production uses)."""
    import ai_backend

    # The CLI default is "small"; this driver does not expose the large tier
    # without an explicit --model, so the literal cast is safe.
    tier: Literal["small", "large"] = "large" if model_tier == "large" else "small"
    return ai_backend.run_ai_prompt(
        prompt,
        model=model or None,
        model_tier=tier,
        purpose="prompt-eval",
        timeout=120,
    )


# ---------------------------------------------------------------------------
# Caching (keyed by prompt_id, version, model, case_id)
# ---------------------------------------------------------------------------


def _cache_key(prompt_id: str, version: str, model: str, case_id: str) -> Path:
    raw = f"{prompt_id}@{version}|{model}|{case_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return (
        _CACHE_DIR
        / f"{prompt_id}-{version}-{model or 'default'}-{case_id}-{digest}.json"
    )


def _load_cached(path: Path) -> ScoredCase | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["cached"] = True
    return ScoredCase(**data)


def _save_cached(path: Path, result: ScoredCase) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def run_eval(
    prompt_id: str,
    model: str | None,
    model_tier: str,
    limit: int | None,
    use_cache: bool,
    auto_yes: bool,
) -> list[ScoredCase]:
    """Run the eval and return per-case results (cached + fresh)."""
    if prompt_id not in EVALUATORS:
        known = ", ".join(sorted(EVALUATORS))
        console.print(f"[red]Unknown prompt {prompt_id!r}. Known: {known}[/red]")
        return []
    evaluator = EVALUATORS[prompt_id]
    version = evaluator.version_stamp

    cases = evaluator.load_cases(limit=limit)
    if not cases:
        console.print(
            f"[red]No golden cases found for {prompt_id!r} "
            f"(expected under tests/fixtures/prompts/golden/{prompt_id}/)[/red]"
        )
        return []

    # Projected call count (excluding cache hits) — cost-control gate.
    fresh_cases = [
        c
        for c in cases
        if not (
            use_cache
            and _load_cached(_cache_key(prompt_id, version, model or "", c.case_id))
        )
    ]
    projected = len(fresh_cases)
    console.print(
        f"\n[bold]Prompt:[/bold] {prompt_id}   [bold]Model:[/bold] {model or model_tier}   "
        f"[bold]Cases:[/bold] {len(cases)} ({projected} fresh, "
        f"{len(cases) - projected} cached)"
    )
    if projected > 0:
        console.print(
            f"[yellow]Projected AI calls: {projected} "
            f"(~{projected} billable invocations)[/yellow]"
        )
    if projected >= _CONFIRM_THRESHOLD and not auto_yes:
        if not Confirm.ask(f"Run {projected} AI calls?", default=False):
            console.print("[yellow]Aborted.[/yellow]")
            return []

    results: list[ScoredCase] = []
    for case in cases:
        cpath = _cache_key(prompt_id, version, model or "", case.case_id)
        if use_cache:
            cached = _load_cached(cpath)
            if cached is not None:
                cached.prompt_id = prompt_id
                cached.prompt_version = version
                cached.model = model or model_tier
                results.append(cached)
                console.log(f"  [dim]cached[/dim] {case.case_id} score={cached.score}")
                continue
        try:
            prompt_text = evaluator.render(case)
            raw = _call_ai(prompt_text, model, model_tier) or ""
            parsed = evaluator.parse(raw)
            score, checks = evaluator.score(parsed, case)
            result = ScoredCase(
                case_id=case.case_id,
                score=score,
                checks=checks,
                raw_output=raw[:500],
            )
        except Exception as exc:  # noqa: BLE001
            result = ScoredCase(
                case_id=case.case_id,
                error=str(exc),
            )
        result.prompt_id = prompt_id
        result.prompt_version = version
        result.model = model or model_tier
        if use_cache and not result.error:
            _save_cached(cpath, result)
        results.append(result)
        tag = (
            f"[green]score={result.score}[/green]"
            if not result.error
            else "[red]error[/red]"
        )
        console.log(f"  {case.case_id}: {tag}")

    return results


def display_results(results: list[ScoredCase], prompt_id: str) -> None:
    if not results:
        return
    # Columns = Case + one per check (union across results) + Score + Cached.
    check_cols: list[str] = []
    for r in results:
        for k in r.checks:
            if k not in check_cols:
                check_cols.append(k)

    table = Table(
        title=f"Prompt Evaluation — {prompt_id} (rubric, 0-100)",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Case", style="bold")
    for ck in check_cols:
        table.add_column(ck, justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Cached", justify="center", style="dim")

    for r in results:
        if r.error:
            table.add_row(r.case_id, *["[red]ERR[/red]"] * len(check_cols), "-", "")
            continue
        cells = []
        for ck in check_cols:
            v = r.checks.get(ck)
            if v is None:
                cells.append("-")
            elif v >= 1.0:
                cells.append("✓")
            elif v <= 0.0:
                cells.append("✗")
            else:
                cells.append(f"{v:.2f}")
        table.add_row(r.case_id, *cells, f"{r.score:.1f}", "yes" if r.cached else "")
    console.print()
    console.print(table)
    avg = sum(r.score for r in results) / len(results) if results else 0.0
    console.print(f"\n[bold]Mean score: {avg:.1f}[/bold]   (n={len(results)})\n")


def save_json_results(
    results: list[ScoredCase], prompt_id: str, model: str | None
) -> Path:
    """Persist results to a JSON file matching embed_eval's convention."""
    out_dir = _HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"prompt-eval-{prompt_id}-{stamp}.json"
    payload = {
        "metadata": {
            "generated_at": datetime.datetime.now().isoformat(),
            "prompt_id": prompt_id,
            "model": model or "default-small",
            "case_count": len(results),
            "mean_score": round(sum(r.score for r in results) / len(results), 2)
            if results
            else 0.0,
        },
        "results": [asdict(r) for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score an externalized prompt against its golden case set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prompt",
        default="summarize-session",
        help="Prompt id to evaluate (default: summarize-session). "
        f"Known: {', '.join(sorted(EVALUATORS))}.",
    )
    parser.add_argument(
        "--model", default=None, help="Explicit model id. Default: the small tier."
    )
    parser.add_argument(
        "--model-tier",
        default="small",
        help="Model tier when --model is unset (default: small).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N golden cases.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached results; re-run every case.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the confirmation prompt for large runs.",
    )
    args = parser.parse_args()

    results = run_eval(
        prompt_id=args.prompt,
        model=args.model,
        model_tier=args.model_tier,
        limit=args.limit,
        use_cache=not args.no_cache,
        auto_yes=args.yes,
    )
    display_results(results, args.prompt)
    out = save_json_results(results, args.prompt, args.model)
    console.print(f"[dim]Results saved to {out}[/dim]")


if __name__ == "__main__":
    main()
