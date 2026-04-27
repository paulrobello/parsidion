#!/usr/bin/env python3
"""Standalone trigger eval for the parsidion skill.

Tests whether Claude would invoke the skill given its description,
by simulating the skill selection decision that happens in interactive sessions.

Run from a SEPARATE terminal (not inside Claude Code):
    python3 ~/.claude/skills/parsidion/scripts/run_trigger_eval.py

Writes results to:
    ~/.claude/skills/parsidion/eval_results.json
"""

import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import vault_common

SKILL_DIR = Path.home() / ".claude" / "skills" / "parsidion"
RESULTS_FILE = SKILL_DIR / "eval_results.json"
MODEL = "claude-sonnet-4-6"

RUNS_PER_QUERY = 3
NUM_WORKERS = 6

# Distractor skills to make the eval realistic - these are real skills
# from the user's setup that Claude must distinguish from parsidion
DISTRACTOR_SKILLS: list[dict[str, str]] = [
    {
        "name": "systematic-debugging",
        "description": "Use when encountering any bug, test failure, or unexpected behavior, before proposing fixes",
    },
    {
        "name": "writing-plans",
        "description": "Use when you have a spec or requirements for a multi-step task, before touching code",
    },
    {
        "name": "frontend-design",
        "description": "Create distinctive, production-grade frontend interfaces with high design quality. Use this skill when the user asks to build web components, pages, or applications.",
    },
    {
        "name": "cc-tmux",
        "description": "Enables Claude to discover and manage tmux panes within a cctmux session. Use when running inside tmux to create panes for dev servers, file watchers, test runners.",
    },
    {
        "name": "site-metadata-generator",
        "description": "Comprehensive SEO optimization for web applications. Use when asked to improve search rankings, add meta tags, create structured data, generate sitemaps.",
    },
]

EVAL_QUERIES: list[dict[str, Any]] = [
    # Should trigger
    {
        "query": "ok so I just spent like 2 hours debugging this circular import between main.py and utils.py - turns out moving the import inside the function fixed it. Can you save this somewhere so I don't forget next time?",
        "should_trigger": True,
    },
    {
        "query": "before we start building the API, check if there's anything in the vault about FastAPI middleware patterns - I feel like we figured out something useful last month",
        "should_trigger": True,
    },
    {
        "query": "I keep running into this same SQLAlchemy connection pool exhaustion issue across different projects. Let's document the fix with the QueuePool settings so it's available everywhere",
        "should_trigger": True,
    },
    {
        "query": "remember that textual apps need to write logs to files, not stdout - I keep forgetting this and it breaks the TUI every time",
        "should_trigger": True,
    },
    {
        "query": "we just did a bunch of research on how Rust's borrow checker handles async closures - save those findings to ClaudeVault so future sessions have that context",
        "should_trigger": True,
    },
    {
        "query": "I discovered that pyright handles generic type narrowing differently than mypy when using match statements. This is worth recording as a pattern for the team",
        "should_trigger": True,
    },
    {
        "query": "update the existing vault note about next.js caching - we found out that the revalidateTag approach is way more reliable than time-based revalidation for our use case",
        "should_trigger": True,
    },
    {
        "query": "so for the parsidion project, the key architectural decision is that all state flows through WorkspaceStore and views subscribe reactively. Make sure this is captured as project context",
        "should_trigger": True,
    },
    {
        "query": "what do we already know about setting up GitHub Actions for Rust cross-compilation? Check our notes before I start from scratch",
        "should_trigger": True,
    },
    {
        "query": "the error 'cannot find type OpaqueTypedef in this scope' in Swift means you need to import the bridging header explicitly when using SPM instead of xcodeproj - this tripped me up for hours, please save it",
        "should_trigger": True,
    },
    # Should NOT trigger
    {
        "query": "can you write a Python function that reads a YAML config file and returns a typed dataclass? I want it to handle missing keys gracefully with defaults",
        "should_trigger": False,
    },
    {
        "query": "I'm building an Obsidian plugin that adds a custom view for visualizing note connections as a force-directed graph. Can you help me set up the plugin scaffold with the right TypeScript types?",
        "should_trigger": False,
    },
    {
        "query": "fix the failing test in tests/test_auth.py - it looks like the mock for the JWT token validator isn't matching the new signature we changed last commit",
        "should_trigger": False,
    },
    {
        "query": "can you add proper docstrings to all the public methods in src/api/routes.py? Google style, include Args and Returns sections",
        "should_trigger": False,
    },
    {
        "query": "I need to set up a knowledge graph using Neo4j for our product recommendation engine. Can you write the Cypher queries for the user-product-category relationships?",
        "should_trigger": False,
    },
    {
        "query": "create a comprehensive README.md for this project - include installation steps, usage examples, the API reference, and a contributing guide",
        "should_trigger": False,
    },
    {
        "query": "search the codebase for everywhere we're using the deprecated `asyncio.coroutine` decorator and migrate them to async/await syntax",
        "should_trigger": False,
    },
    {
        "query": "remember to add input validation for the email field before we ship this - regex or use a library, whatever's more robust",
        "should_trigger": False,
    },
    {
        "query": "I want to store user preferences in a YAML file under ~/.config/myapp/ - can you write the load/save functions with proper XDG path handling?",
        "should_trigger": False,
    },
    {
        "query": "set up a markdown-based documentation site using mkdocs-material for our internal API. I want it to auto-generate from our OpenAPI spec and include a changelog section",
        "should_trigger": False,
    },
]


def parse_skill_frontmatter() -> tuple[str, str]:
    """Parse name and description from SKILL.md frontmatter.

    Uses ``vault_common.parse_frontmatter`` which supports multi-line
    scalar block indicators (``>``, ``|``, ``>-``, ``|-``).
    """
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    fm = vault_common.parse_frontmatter(content)
    name = str(fm.get("name", ""))
    description = str(fm.get("description", ""))
    return name, description


def build_eval_prompt(query: str, skill_name: str, skill_description: str) -> str:
    """Build a prompt that simulates the skill selection decision.

    Mimics how Claude Code presents available skills in the system prompt
    and asks the model to decide whether to invoke a specific skill.
    """
    skill_list_parts = [f"- {skill_name}: {skill_description}"]
    for distractor in DISTRACTOR_SKILLS:
        skill_list_parts.append(f"- {distractor['name']}: {distractor['description']}")

    skill_list = "\n".join(skill_list_parts)

    return f"""You are Claude Code. Before responding to user messages, you check if any available skill should be consulted. Skills provide specialized workflows and domain knowledge.

Available skills:
{skill_list}

A user sends this message:
"{query}"

Should you invoke the "{skill_name}" skill before responding? Consider:
- Does the message match the skill's trigger conditions?
- Would the skill provide value the user needs?
- Is this clearly within the skill's domain, or is it a different task entirely?

Answer with ONLY one word: YES or NO"""


def run_single_query(
    query: str,
    skill_name: str,
    skill_description: str,
) -> bool:
    """Run a single query and return whether the model would trigger the skill."""
    prompt = build_eval_prompt(query, skill_name, skill_description)

    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--model", MODEL, "--no-session-persistence"],
            capture_output=True,
            text=True,
            timeout=30,
            env=vault_common.env_without_claudecode(),
        )
        response = result.stdout.strip().upper()
        # Check for YES anywhere in the response (model might add explanation)
        return "YES" in response and "NO" not in response.split("YES")[0]
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
        print(f"  Warning: query failed: {e}", file=sys.stderr)
        return False


def main() -> None:
    """Run the full eval and write results."""
    skill_name, skill_description = parse_skill_frontmatter()

    print(f"Skill: {skill_name}")
    print(f"Description: {skill_description[:100]}...")
    print(f"Model: {MODEL}")
    print(
        f"Queries: {len(EVAL_QUERIES)} x {RUNS_PER_QUERY} runs = {len(EVAL_QUERIES) * RUNS_PER_QUERY} total"
    )
    print(f"Workers: {NUM_WORKERS}")
    print("Method: Direct skill-selection simulation")
    print("=" * 60)

    results: list[dict[str, Any]] = []
    query_triggers: dict[str, list[bool]] = {}
    query_items: dict[str, dict[str, Any]] = {}

    t0 = time.time()

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        future_to_info: dict[Any, tuple[dict[str, Any], int]] = {}
        for item in EVAL_QUERIES:
            for run_idx in range(RUNS_PER_QUERY):
                future = executor.submit(
                    run_single_query,
                    item["query"],
                    skill_name,
                    skill_description,
                )
                future_to_info[future] = (item, run_idx)

        done_count = 0
        total_count = len(future_to_info)
        for future in as_completed(future_to_info):
            item, _ = future_to_info[future]
            query = item["query"]
            query_items[query] = item
            if query not in query_triggers:
                query_triggers[query] = []
            try:
                query_triggers[query].append(future.result())
            except Exception as e:  # noqa: BLE001
                print(f"  Warning: query failed: {e}", file=sys.stderr)
                query_triggers[query].append(False)
            done_count += 1
            if done_count % 5 == 0:
                print(f"  Progress: {done_count}/{total_count} runs complete...")

    elapsed = time.time() - t0

    for query, triggers in query_triggers.items():
        item = query_items[query]
        trigger_rate = sum(triggers) / len(triggers)
        should_trigger = item["should_trigger"]
        if should_trigger:
            did_pass = trigger_rate >= 0.5
        else:
            did_pass = trigger_rate < 0.5

        results.append(
            {
                "query": query,
                "should_trigger": should_trigger,
                "trigger_rate": trigger_rate,
                "triggers": sum(triggers),
                "runs": len(triggers),
                "pass": did_pass,
            }
        )

    # Sort: should-trigger first, then should-not-trigger
    results.sort(key=lambda r: (not r["should_trigger"], r["query"]))

    passed = sum(1 for r in results if r["pass"])
    total = len(results)

    # Compute precision/recall
    pos = [r for r in results if r["should_trigger"]]
    neg = [r for r in results if not r["should_trigger"]]
    tp = sum(r["triggers"] for r in pos)
    pos_runs = sum(r["runs"] for r in pos)
    fn = pos_runs - tp
    fp = sum(r["triggers"] for r in neg)
    neg_runs = sum(r["runs"] for r in neg)
    tn = neg_runs - fp
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    accuracy = (tp + tn) / (tp + tn + fp + fn) if (tp + tn + fp + fn) > 0 else 0.0

    output = {
        "skill_name": skill_name,
        "description": skill_description,
        "model": MODEL,
        "runs_per_query": RUNS_PER_QUERY,
        "eval_method": "skill-selection-simulation",
        "elapsed_seconds": round(elapsed, 1),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "accuracy": round(accuracy, 3),
            "true_positives": tp,
            "false_positives": fp,
            "true_negatives": tn,
            "false_negatives": fn,
        },
        "results": results,
    }

    # Print summary
    print()
    print("=" * 60)
    print(f"Results: {passed}/{total} queries passed in {elapsed:.1f}s")
    print(f"Precision: {precision:.0%}  Recall: {recall:.0%}  Accuracy: {accuracy:.0%}")
    print(f"TP={tp} FP={fp} TN={tn} FN={fn}")
    print()

    for r in results:
        status = "PASS" if r["pass"] else "FAIL"
        rate = f"{r['triggers']}/{r['runs']}"
        expected = "should" if r["should_trigger"] else "should NOT"
        print(f"  [{status}] rate={rate} ({expected} trigger): {r['query'][:70]}")

    # Write results
    RESULTS_FILE.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nResults written to: {RESULTS_FILE}")


if __name__ == "__main__":
    main()
