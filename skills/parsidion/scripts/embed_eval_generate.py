#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""Phase 1: Ground-truth query generation for the embedding eval harness.

Samples vault notes and uses Claude to generate search queries that serve
as the ground-truth evaluation dataset.
"""

import json
import random
import re
import subprocess
import sys
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from rich.progress import (  # type: ignore[import-untyped]
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

# Ensure sibling scripts are importable
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import vault_common  # noqa: E402
from embed_eval_common import (  # noqa: E402
    CLAUDE_TIMEOUT,
    EvalItem,
    _note_title,
    console,
)


# ---------------------------------------------------------------------------
# Claude helper
# ---------------------------------------------------------------------------


def _call_claude(prompt: str, timeout: int = CLAUDE_TIMEOUT) -> str | None:
    """Call `claude -p` with CLAUDECODE unset. Returns stdout or None."""
    env = vault_common.env_without_claudecode()
    try:
        result = subprocess.run(
            ["claude", "-p", prompt, "--no-session-persistence"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------


def generate_queries_for_note(
    note_path: Path,
    queries_per_note: int,
) -> list[str]:
    """Ask Claude to generate *queries_per_note* search queries for the note."""
    try:
        content = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    body = vault_common.get_body(content).strip()
    fm = vault_common.parse_frontmatter(content)
    title = _note_title(note_path, content)
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]
    tags_str = ", ".join(str(t) for t in tags) if tags else "none"

    prompt = (
        f"You are generating evaluation queries for a semantic search benchmark.\n\n"
        f"Below is a vault note. Generate exactly {queries_per_note} distinct search "
        f"queries that a developer would type to find this specific note.\n\n"
        f"Rules:\n"
        f"- Vary specificity: include at least one broad and one specific query\n"
        f"- Use natural language (not keywords only)\n"
        f"- Do NOT include the exact note title as a query\n"
        f'- Return ONLY a JSON object: {{"queries": ["q1", "q2", ...]}}\n\n'
        f"Note title: {title}\n"
        f"Tags: {tags_str}\n"
        f"Content snippet:\n{body[:800]}\n"
    )

    raw = _call_claude(prompt)
    if not raw:
        return []

    json_match = re.search(r'\{[^{}]*"queries"[^{}]*\}', raw, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            queries = data.get("queries", [])
            if isinstance(queries, list):
                return [str(q) for q in queries[:queries_per_note] if q]
        except json.JSONDecodeError:
            pass
    return []


def generate_ground_truth(
    notes_sample: int,
    queries_per_note: int,
    output_file: Path,
    seed: int = 42,
) -> list[EvalItem]:
    """Sample notes, generate queries via Claude, save to YAML, return items."""
    all_notes = vault_common.all_vault_notes()
    non_daily = [n for n in all_notes if "Daily" not in n.parts]

    rng = random.Random(seed)
    sample = rng.sample(non_daily, min(notes_sample, len(non_daily)))

    items: list[EvalItem] = []
    failed = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generating queries via Claude", total=len(sample))
        for note_path in sample:
            queries = generate_queries_for_note(note_path, queries_per_note)
            if queries:
                items.append(
                    EvalItem(stem=note_path.stem, path=str(note_path), queries=queries)
                )
            else:
                failed += 1
            progress.advance(task)

    if failed:
        console.print(
            f"[yellow]Warning: {failed} notes failed query generation[/yellow]"
        )

    output_file.parent.mkdir(parents=True, exist_ok=True)
    data = [{"stem": i.stem, "path": i.path, "queries": i.queries} for i in items]
    output_file.write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    console.print(f"[green]Saved {len(items)} eval items -> {output_file}[/green]")
    return items


def load_ground_truth(queries_file: Path) -> list[EvalItem]:
    """Load ground-truth items from a YAML file."""
    raw = yaml.safe_load(queries_file.read_text(encoding="utf-8"))
    return [EvalItem(stem=e["stem"], path=e["path"], queries=e["queries"]) for e in raw]
