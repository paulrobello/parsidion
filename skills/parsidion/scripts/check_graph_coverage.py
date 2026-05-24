#!/usr/bin/env python3
"""Audit vault tag coverage against Obsidian graph.json color groups.

Reports:
  - Vault tags NOT covered by any color group (sorted by frequency)
  - Graph group tags NOT found in the vault (stale entries)

Usage:
    python check_graph_coverage.py
    python check_graph_coverage.py --json          # machine-readable output
    python check_graph_coverage.py --threshold 2   # only show tags used >= 2 times
"""

import argparse
import json
import re
import sys
from pathlib import Path

from vault_common import VAULT_ROOT

GRAPH_JSON: Path = VAULT_ROOT / ".obsidian" / "graph.json"
CLAUDE_MD: Path = VAULT_ROOT / "CLAUDE.md"

# Regex to extract tags from a graph.json query string
# Matches: tag:#some-tag-name
_TAG_RE = re.compile(r"tag:#([\w\-]+)")

# Regex to extract tag counts from the Tag Cloud line
# Matches: `tagname` (count)
_TAG_CLOUD_RE = re.compile(r"`([\w\-]+)`\s*\((\d+)\)")


def load_graph_tags() -> dict[str, list[str]]:
    """Load color groups from graph.json.

    Returns:
        Dict mapping group query string to list of tag names covered.
    """
    if not GRAPH_JSON.is_file():
        print(f"Error: {GRAPH_JSON} not found", file=sys.stderr)
        sys.exit(1)

    with open(GRAPH_JSON, encoding="utf-8") as f:
        data = json.load(f)

    groups: dict[str, list[str]] = {}
    for group in data.get("colorGroups", []):
        query = group.get("query", "")
        tags = _TAG_RE.findall(query)
        groups[query] = tags

    return groups


def load_vault_tag_counts() -> dict[str, int]:
    """Parse tag counts from the ## Tag Cloud section of TAGS.md.

    Falls back to CLAUDE.md for older vaults.

    Returns:
        Dict mapping tag name to usage count.
    """
    tags_path = CLAUDE_MD.parent / "TAGS.md"
    for path in (tags_path, CLAUDE_MD):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        tag_cloud_match = re.search(r"## Tag Cloud\n(.*?)\n", content, re.DOTALL)
        if tag_cloud_match:
            tag_cloud_line = tag_cloud_match.group(1)
            counts: dict[str, int] = {}
            for tag, count_str in _TAG_CLOUD_RE.findall(tag_cloud_line):
                counts[tag] = int(count_str)
            return counts

    print(
        "Error: '## Tag Cloud' section not found in TAGS.md or CLAUDE.md. "
        "Run update_index.py first.",
        file=sys.stderr,
    )
    sys.exit(1)


def load_vault_tags() -> set[str]:
    """Parse the ## Existing Tags section of TAGS.md for the authoritative tag list.

    Falls back to CLAUDE.md for older vaults.

    Returns:
        Set of all tag names present in the vault.
    """
    tags_path = CLAUDE_MD.parent / "TAGS.md"
    for path in (tags_path, CLAUDE_MD):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        match = re.search(r"## Existing Tags\n(.*?)(?:\n\n|\n##|$)", content, re.DOTALL)
        if match:
            line = match.group(1).strip()
            return {tag.strip() for tag in line.split(",") if tag.strip()}
    return set()


def main() -> None:
    """Run the graph coverage audit."""
    parser = argparse.ArgumentParser(
        description="Audit Obsidian graph color group coverage against vault tags."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON for scripting",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=1,
        metavar="N",
        help="Only report uncovered tags used at least N times (default: 1)",
    )
    args = parser.parse_args()

    # Load data
    groups = load_graph_tags()
    tag_counts = load_vault_tag_counts()
    all_vault_tags = load_vault_tags()

    # Build set of covered tags (from graph.json)
    covered: set[str] = set()
    for tags in groups.values():
        covered.update(tags)

    # Uncovered vault tags (in vault but not in any color group)
    uncovered: list[tuple[str, int]] = []
    for tag in all_vault_tags:
        if tag not in covered:
            count = tag_counts.get(tag, 0)
            if count >= args.threshold:
                uncovered.append((tag, count))
    # Sort by count descending, then alphabetically
    uncovered.sort(key=lambda x: (-x[1], x[0]))

    # Stale graph tags (in graph.json but not in vault)
    stale: list[str] = sorted(t for t in covered if t not in all_vault_tags)

    if args.json:
        result = {
            "uncovered": [{"tag": t, "count": c} for t, c in uncovered],
            "stale": stale,
            "stats": {
                "total_vault_tags": len(all_vault_tags),
                "covered": len(covered & all_vault_tags),
                "uncovered": len(uncovered),
                "stale": len(stale),
            },
        }
        print(json.dumps(result, indent=2))
        return

    # Human-readable output
    total = len(all_vault_tags)
    n_covered = len(covered & all_vault_tags)
    coverage_pct = (n_covered / total * 100) if total else 0

    print("\nGraph Coverage Audit")
    print(f"{'=' * 50}")
    print(f"Vault tags total : {total}")
    print(f"Covered by groups: {n_covered} ({coverage_pct:.0f}%)")
    print(f"Uncovered        : {len(uncovered)}")
    print(f"Stale (in graph, not vault): {len(stale)}")

    if uncovered:
        print(f"\n{'─' * 50}")
        print(f"Uncovered vault tags (threshold >= {args.threshold}):")
        print(f"{'─' * 50}")
        print(f"{'Tag':<30} {'Count':>6}  Suggested Group")
        print(f"{'─' * 50}")
        for tag, count in uncovered:
            # Suggest a group based on simple heuristics
            suggestion = _suggest_group(tag)
            print(f"  {tag:<28} {count:>6}  → {suggestion}")
    else:
        print("\nAll vault tags are covered by color groups!")

    if stale:
        print(f"\n{'─' * 50}")
        print("Stale graph tags (no matching vault notes):")
        print(f"{'─' * 50}")
        for tag in stale:
            print(f"  {tag}")

    print()


def _suggest_group(tag: str) -> str:
    """Suggest a color group for an uncovered tag using simple keyword heuristics.

    Args:
        tag: The tag name to classify.

    Returns:
        A suggested group label string.
    """
    t = tag.lower()
    if any(
        k in t
        for k in (
            "rust",
            "python",
            "swift",
            "typescript",
            "nextjs",
            "react",
            "macos",
            "lang",
        )
    ):
        return "Languages"
    if any(k in t for k in ("term", "terminal", "ansi", "pty", "vt", "xterm")):
        return "Terminal"
    if any(
        k in t
        for k in (
            "wgpu",
            "sdf",
            "voxel",
            "fractal",
            "mandel",
            "vrm",
            "avatar",
            "3d",
            "glsl",
            "shader",
        )
    ):
        return "Graphics / 3D"
    if any(k in t for k in ("debug", "fix", "error", "bug", "crash", "issue")):
        return "Debugging"
    if any(k in t for k in ("pattern", "arch", "design", "memory", "migrat")):
        return "Patterns"
    if any(k in t for k in ("research", "study", "analysis", "survey")):
        return "Research"
    if any(
        k in t
        for k in (
            "mcp",
            "claude",
            "ollama",
            "llm",
            "ai",
            "model",
            "sdk",
            "api",
            "tool",
            "cli",
        )
    ):
        return "Tools / AI"
    # Project-specific tags often look like project names
    return "Projects (or new group)"


if __name__ == "__main__":
    main()
