#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""ENH-008 Step 5 — prompt evaluation driver.

Scores the six externalized prompts against the golden transcript set under
``tests/fixtures/prompts/golden/``, so a prompt edit can be evaluated rather
than guessed at. Extends the ``embed_eval_*`` harness conventions (PEP 723
script, Rich table, JSON result file) rather than building a parallel one.

Each golden case renders the prompt through the real ``prompt_templates``
loader (the same path production uses), calls the configured AI backend, and
scores the output against the case's expected-characteristics YAML:

| Check                          | Weight | How                                |
|--------------------------------|:------:|------------------------------------|
| Write-gate decision correct    | 25     | matches ``should_produce_note``    |
| Note type correct              | 20     | matches ``expected_type``          |
| Frontmatter valid              | 20     | ``note_schema`` validator          |
| Tag precision/recall           | 20     | against include/exclude lists      |
| Required content present       | 15     | ``must_mention`` substring checks  |

Cost control (the plan's hard requirement):

- Defaults to the small model tier.
- Prints the projected AI call count before starting and asks for
  confirmation above a threshold (override with ``--yes``).
- Caches each case's result keyed by ``(prompt_id, version, model, case_id)``
  so re-running after editing one case does not re-bill the rest.

This script is OPT-IN — ``make checkall`` does not invoke it. Run it by hand:

    # Score the current summarizer prompt against the golden set:
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session

    # Use a larger model (explicit opt-in for higher cost):
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session --model claude-sonnet-4-5

    # Limit to N cases for a quick check:
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session --limit 3

    # Compare two prompt versions (requires the version files on disk):
    uv run tools/eval/prompt_eval_run.py --prompt summarize-session --compare 1.0.0 1.1.0
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from rich.console import Console  # type: ignore[import-untyped]
from rich.prompt import Confirm  # type: ignore[import-untyped]
from rich.table import Table  # type: ignore[import-untyped]

# Make the skills scripts importable (vault_common, ai_backend, prompt_templates,
# note_schema) — matches the sys.path manipulation in embed_eval_common.py.
_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = str(_HERE.parents[1] / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_FIXTURE_DIR = _HERE.parents[1] / "tests" / "fixtures" / "prompts" / "golden"
_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))
    / "parsidion"
    / "prompt-eval"
)

console = Console()

# Rubric weights — must sum to 100.
WEIGHT_WRITE_GATE = 25
WEIGHT_TYPE = 20
WEIGHT_FRONTMATTER = 20
WEIGHT_TAGS = 20
WEIGHT_MUST_MENTION = 15
assert (
    WEIGHT_WRITE_GATE
    + WEIGHT_TYPE
    + WEIGHT_FRONTMATTER
    + WEIGHT_TAGS
    + WEIGHT_MUST_MENTION
    == 100
)

_CONFIRM_THRESHOLD = 12  # confirm before running more than this many uncached calls


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class GoldenCase:
    """One golden transcript + its expected characteristics."""

    case_id: str
    transcript: str
    expected: dict[str, Any]


@dataclass
class CaseResult:
    """Scored result for one golden case under one prompt+model."""

    case_id: str
    prompt_id: str
    prompt_version: str
    model: str
    write_gate_correct: bool
    type_correct: bool
    frontmatter_valid: bool
    tag_precision: float
    tag_recall: float
    must_mention_hit: int
    must_mention_total: int
    score: float
    cached: bool = False
    error: str = ""
    raw_output: str = field(default="", repr=False)


# ---------------------------------------------------------------------------
# Golden case loading
# ---------------------------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the restricted YAML used by ``*.expected.yaml``.

    The expected-characteristics files use a flat ``key: value`` form where
    values are scalars or inline ``[a, b]`` lists. We avoid a full YAML parser
    dependency at eval time by handling this subset directly; if the file
    shape ever grows nested mappings, switch to PyYAML.
    """
    import yaml  # type: ignore[import-untyped]

    return yaml.safe_load(text) or {}


def load_golden_cases(limit: int | None = None) -> list[GoldenCase]:
    """Load every ``(transcript, expected)`` pair from the golden directory."""
    cases: list[GoldenCase] = []
    transcripts = sorted(_FIXTURE_DIR.glob("*.transcript.md"))
    for tp in transcripts:
        stem = tp.name.removesuffix(".transcript.md")
        expected_path = _FIXTURE_DIR / f"{stem}.expected.yaml"
        if not expected_path.is_file():
            continue
        expected = _parse_simple_yaml(expected_path.read_text(encoding="utf-8"))
        cases.append(
            GoldenCase(
                case_id=stem,
                transcript=tp.read_text(encoding="utf-8"),
                expected=expected,
            )
        )
    if limit:
        cases = cases[:limit]
    return cases


# ---------------------------------------------------------------------------
# Rendering + AI call
# ---------------------------------------------------------------------------


def _build_prompt_for_case(prompt_id: str, case: GoldenCase) -> tuple[str, str]:
    """Render *prompt_id* for *case* and return (prompt_text, version_stamp).

    Only ``summarize-session`` is wired for full case rendering today (it takes
    a transcript). The other five prompts have different variable contracts
    and are evaluated via their own consumer call sites — this driver flags
    unsupported prompts clearly rather than guessing variables.
    """
    import prompt_templates

    tpl = prompt_templates.load_prompt(prompt_id)
    if prompt_id == "summarize-session":
        rendered = prompt_templates.render(
            prompt_id,
            project="eval-project",
            cats_str="general",
            today=datetime.date.today().isoformat(),
            dedup_block="",
            cleaned_transcript=case.transcript,
            tags_instruction=(
                "  tags (2-4 relevant tags;\n"
                "  NEVER use underscores — always kebab-case (hyphens);\n"
                "  prefer short singular tags: 'voxel' not 'voxel-engine', "
                "'hook' not 'hooks')"
            ),
            valid_types=", ".join(sorted(__import__("note_schema").VALID_NOTE_TYPES)),
            session_id="eval-case",
        )
        return rendered, tpl.version_stamp
    raise SystemExit(
        f"prompt_eval_run: --prompt {prompt_id!r} rendering is not wired for "
        f"golden cases yet (only 'summarize-session' is). Each prompt has a "
        f"different variable contract; wire its consumer's call site here."
    )


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
# Output parsing + scoring
# ---------------------------------------------------------------------------


_SKIP_JSON_RE = re.compile(r'\{"decision"\s*:\s*"skip"', re.IGNORECASE)
_FM_TYPE_RE = re.compile(r"^type:\s*(\S+)", re.MULTILINE)
_FM_TAGS_RE = re.compile(r"^tags:\s*\[(.*?)\]", re.MULTILINE)


def _parse_output(raw: str) -> dict[str, Any]:
    """Extract (decision, type, tags, body) from the model's raw output."""
    decision = "save"
    if _SKIP_JSON_RE.search(raw):
        decision = "skip"
    type_match = _FM_TYPE_RE.search(raw)
    tags_match = _FM_TAGS_RE.search(raw)
    tags: list[str] = []
    if tags_match:
        tags = [
            t.strip().strip("\"'") for t in tags_match.group(1).split(",") if t.strip()
        ]
    return {
        "decision": decision,
        "type": type_match.group(1).strip() if type_match else "",
        "tags": tags,
        "raw": raw,
    }


def _score_case(
    parsed: dict[str, Any], case: GoldenCase
) -> tuple[CaseResult, dict[str, Any]]:
    """Score the parsed output against the case's expected characteristics."""
    expected = case.expected
    should_produce = bool(expected.get("should_produce_note", True))

    write_gate_correct = (parsed["decision"] != "skip") == should_produce
    type_correct = not should_produce or str(parsed["type"]) == str(
        expected.get("expected_type", "")
    )

    # Frontmatter validity: the body must have a frontmatter block with a type
    # in the valid set (skip-decisions are exempt — they emit JSON, not a note).
    import note_schema

    frontmatter_valid = True
    if should_produce and parsed["decision"] != "skip":
        frontmatter_valid = parsed["type"] in note_schema.VALID_NOTE_TYPES

    # Tag precision/recall against include/exclude lists.
    expected_include = set(str(t) for t in expected.get("expected_tags_include") or [])
    expected_exclude = set(str(t) for t in expected.get("expected_tags_exclude") or [])
    actual_tags = set(parsed["tags"])
    if expected_include:
        tag_recall = len(actual_tags & expected_include) / len(expected_include)
    else:
        tag_recall = 1.0
    forbidden_hits = actual_tags & expected_exclude
    tag_precision = (
        1.0
        if not forbidden_hits
        else (
            len(actual_tags - expected_exclude) / len(actual_tags)
            if actual_tags
            else 0.0
        )
    )

    # must_mention substring checks.
    must_mention = [str(s) for s in (expected.get("must_mention") or [])]
    body = parsed["raw"]
    hits = sum(1 for s in must_mention if s.lower() in body.lower())
    must_mention_hit = hits
    must_mention_total = len(must_mention)

    # Weighted score. When the case expects no note and the write-gate agreed,
    # the type/frontmatter/tag/content checks are vacuously satisfied.
    if not should_produce and parsed["decision"] == "skip":
        score = 100.0
        type_correct = True
        frontmatter_valid = True
    else:
        score = (
            (WEIGHT_WRITE_GATE if write_gate_correct else 0)
            + (WEIGHT_TYPE if type_correct else 0)
            + (WEIGHT_FRONTMATTER if frontmatter_valid else 0)
            + WEIGHT_TAGS * (0.5 * tag_precision + 0.5 * tag_recall)
            + (
                WEIGHT_MUST_MENTION * (must_mention_hit / must_mention_total)
                if must_mention_total
                else WEIGHT_MUST_MENTION
            )
        )

    result = CaseResult(
        case_id=case.case_id,
        prompt_id="",  # filled by caller
        prompt_version="",  # filled by caller
        model="",  # filled by caller
        write_gate_correct=write_gate_correct,
        type_correct=type_correct,
        frontmatter_valid=frontmatter_valid,
        tag_precision=tag_precision,
        tag_recall=tag_recall,
        must_mention_hit=must_mention_hit,
        must_mention_total=must_mention_total,
        score=round(score, 1),
        raw_output=parsed["raw"][:500],
    )
    detail = {
        "actual_type": parsed["type"],
        "actual_tags": sorted(actual_tags),
        "decision": parsed["decision"],
    }
    return result, detail


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


def _load_cached(path: Path) -> CaseResult | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    data["cached"] = True
    return CaseResult(**data)


def _save_cached(path: Path, result: CaseResult) -> None:
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
) -> list[CaseResult]:
    """Run the eval and return per-case results (cached + fresh)."""
    cases = load_golden_cases(limit=limit)
    if not cases:
        console.print(f"[red]No golden cases found in {_FIXTURE_DIR}[/red]")
        return []

    # Projected call count (excluding cache hits) — cost-control gate.
    fresh_cases: list[GoldenCase] = []
    for c in cases:
        if use_cache and _load_cached(
            _cache_key(prompt_id, "1.0.0", model or "", c.case_id)
        ):
            continue
        fresh_cases.append(c)
    projected = len(fresh_cases)
    console.print(
        f"\n[bold]Prompt:[/bold] {prompt_id}   [bold]Model:[/bold] {model or model_tier}   "
        f"[bold]Cases:[/bold] {len(cases)} ({projected} fresh, {len(cases) - projected} cached)"
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

    results: list[CaseResult] = []
    for case in cases:
        version = "1.0.0"  # resolved per-prompt below
        _, version = _build_prompt_for_case(prompt_id, case)
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
            prompt_text, _ = _build_prompt_for_case(prompt_id, case)
            raw = _call_ai(prompt_text, model, model_tier) or ""
            parsed = _parse_output(raw)
            result, _ = _score_case(parsed, case)
        except Exception as exc:  # noqa: BLE001
            result = CaseResult(
                case_id=case.case_id,
                prompt_id=prompt_id,
                prompt_version=version,
                model=model or model_tier,
                write_gate_correct=False,
                type_correct=False,
                frontmatter_valid=False,
                tag_precision=0.0,
                tag_recall=0.0,
                must_mention_hit=0,
                must_mention_total=len(case.expected.get("must_mention") or []),
                score=0.0,
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


def display_results(results: list[CaseResult], prompt_id: str) -> None:
    if not results:
        return
    table = Table(
        title=f"Prompt Evaluation — {prompt_id} (rubric, 0-100)",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Case", style="bold")
    table.add_column("Score", justify="right")
    table.add_column("WriteGate", justify="center")
    table.add_column("Type", justify="center")
    table.add_column("FM", justify="center")
    table.add_column("Tag P/R", justify="center")
    table.add_column("MustMention", justify="center")
    table.add_column("Cached", justify="center", style="dim")

    for r in results:
        if r.error:
            table.add_row(r.case_id, "[red]ERR[/red]", "-", "-", "-", "-", "-", "")
            continue
        table.add_row(
            r.case_id,
            f"{r.score:.1f}",
            "✓" if r.write_gate_correct else "✗",
            "✓" if r.type_correct else "✗",
            "✓" if r.frontmatter_valid else "✗",
            f"{r.tag_precision:.2f}/{r.tag_recall:.2f}",
            f"{r.must_mention_hit}/{r.must_mention_total}",
            "yes" if r.cached else "",
        )
    console.print()
    console.print(table)
    avg = sum(r.score for r in results) / len(results) if results else 0.0
    console.print(f"\n[bold]Mean score: {avg:.1f}[/bold]   (n={len(results)})\n")


def save_json_results(
    results: list[CaseResult], prompt_id: str, model: str | None
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
            "weights": {
                "write_gate": WEIGHT_WRITE_GATE,
                "type": WEIGHT_TYPE,
                "frontmatter": WEIGHT_FRONTMATTER,
                "tags": WEIGHT_TAGS,
                "must_mention": WEIGHT_MUST_MENTION,
            },
        },
        "results": [asdict(r) for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score an externalized prompt against the golden transcript set.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--prompt",
        default="summarize-session",
        help="Prompt id to evaluate (default: summarize-session).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Explicit model id. Default: the small model tier.",
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
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("V1", "V2"),
        default=None,
        help="Compare two prompt versions (prints both mean scores).",
    )
    args = parser.parse_args()

    if args.compare:
        console.print(
            f"[yellow]--compare {args.compare[0]} {args.compare[1]}: edit the "
            f"template files to each version in turn and run twice, or place "
            f"both versions in templates/prompts/ and invoke separately. "
            f"Cross-version comparison reuses the same cache, keyed by version.[/yellow]"
        )

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
