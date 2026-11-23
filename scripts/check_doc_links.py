#!/usr/bin/env python3
"""Gate: fail ``make checkall`` on broken markdown doc links (parsight doc_links).

Asks the live parsight daemon for the high-confidence broken-link worklist
(``parsight call doc_links --args-json '{"mode": "broken"}'``) and exits 1 when
any actionable row survives the exclusion list. The check NEVER fails on
transport or freshness ambiguity: an unreachable daemon, a stale index, a
partial read, or unparseable output all skip with a warning on stderr, so
hosts without parsight (CI) and moments of catch-up indexing stay green. The
failure path requires positive evidence: a parsed payload with healthy
``_meta`` flags and at least one surviving broken link.

Stdlib-only. Excluded sources: ``tests/fixtures/`` (golden prompt content is
data under test, not documentation). The ``<!-- doc-path: ... -->`` waiver
marker is honored upstream by parsight itself and needs no support here. The
check is scoped to the checkout at ``--root``: in a parsight-registered
checkout it verifies that checkout; in an unregistered worktree parsight
rejects the selector and the leg skips, so worktree ``make checkall`` runs
stay green while the main checkout is the one actually gated.

Usage::

    python scripts/check_doc_links.py                 # gate; root = repo root
    python scripts/check_doc_links.py --root <path>   # explicit checkout
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

EXCLUDED_SOURCE_PREFIXES = ("tests/fixtures/",)

TRUST_KEYS = (
    "stale",
    "partial",
    "root_missing",
    "head_unreadable",
    "extractor_stale",
)

EXIT_OK = 0
EXIT_BROKEN = 1


def evaluate(
    meta: dict[str, Any] | None, rows: list[dict[str, Any]]
) -> tuple[str, str]:
    """Decide the gate verdict from a parsed doc_links ``broken`` payload.

    Returns ``(verdict, detail)`` where verdict is ``"fail"``, ``"skip"`` or
    ``"clean"``. ``detail`` is a human-readable line for either outcome.
    """
    if meta is None:
        return "skip", "no _meta in doc_links payload"
    if any(meta.get(key) for key in TRUST_KEYS):
        flagged = ", ".join(key for key in TRUST_KEYS if meta.get(key))
        return "skip", f"index not trustworthy for gating ({flagged})"
    if meta.get("empty_reason"):
        return "skip", f"doc_links empty: {meta.get('empty_reason')}"
    excluded = [
        r
        for r in rows
        if r.get("src_file_path", "").startswith(EXCLUDED_SOURCE_PREFIXES)
    ]
    actionable = [
        r
        for r in rows
        if not r.get("src_file_path", "").startswith(EXCLUDED_SOURCE_PREFIXES)
    ]
    if actionable:
        lines = [
            f"  {r.get('src_file_path')}:{r.get('link_line')} -> {r.get('raw_target')} ({r.get('reason')})"
            for r in actionable
        ]
        return "fail", f"{len(actionable)} broken doc link(s):\n" + "\n".join(lines)
    return "clean", f"0 actionable broken doc links ({len(excluded)} excluded)"


def _repo_slug(root: Path) -> str:
    """Repository identity for parsight scoping: origin basename, else dir name."""
    try:
        url = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
    except (subprocess.SubprocessError, OSError):
        return root.name


def _run_parsight(root: Path, timeout: float, repository_id: str) -> tuple[int, str]:
    proc = subprocess.run(
        [
            "parsight",
            "call",
            "doc_links",
            "--args-json",
            json.dumps(
                {
                    "mode": "broken",
                    "repository_id": repository_id,
                    "worktree": str(root),
                }
            ),
        ],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail on high-confidence broken markdown doc links"
    )
    default_root = Path(__file__).resolve().parent.parent
    parser.add_argument(
        "--root", type=Path, default=default_root, help="checkout to anchor parsight to"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="subprocess timeout in seconds"
    )
    args = parser.parse_args(argv)

    try:
        code, out = _run_parsight(args.root, args.timeout, _repo_slug(args.root))
    except subprocess.TimeoutExpired:
        print("doc-links-check: SKIP — parsight call timed out", file=sys.stderr)
        return EXIT_OK
    except FileNotFoundError:
        print("doc-links-check: SKIP — parsight CLI not found", file=sys.stderr)
        return EXIT_OK
    if code != 0:
        print(f"doc-links-check: SKIP — parsight call exited {code}", file=sys.stderr)
        return EXIT_OK
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        print("doc-links-check: SKIP — unparseable parsight output", file=sys.stderr)
        return EXIT_OK
    meta = payload.get("_meta") if isinstance(payload, dict) else None
    rows = payload.get("broken_links") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        rows = []
    verdict, detail = evaluate(meta if isinstance(meta, dict) else None, rows)
    if verdict == "fail":
        print(f"doc-links-check: FAIL — {detail}")
        return EXIT_BROKEN
    if verdict == "skip":
        print(f"doc-links-check: SKIP — {detail}", file=sys.stderr)
        return EXIT_OK
    print(f"doc-links-check: OK — {detail}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
