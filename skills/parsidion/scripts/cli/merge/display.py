"""Pre-merge human-readable diff summary (ARC-005).

Extracted from ``vault_merge.py``. Re-exported by the entry shim so
``vault_merge._print_diff_summary`` keeps resolving for tests and other
callers.

Stdlib-only at module load.
"""

from __future__ import annotations

from pathlib import Path

import vault_common

from cli.merge.frontmatter import _parse_tags_list


def _print_diff_summary(
    path_a: Path,
    content_a: str,
    path_b: Path,
    content_b: str,
    vault_path: Path | None = None,
) -> None:
    """Print a human-readable diff summary of two notes.

    Args:
        path_a: Path to note A.
        content_a: Content of note A.
        path_b: Path to note B.
        content_b: Content of note B.
        vault_path: Path to the vault root.
    """
    title_a = vault_common.extract_title(content_a, path_a.stem)
    title_b = vault_common.extract_title(content_b, path_b.stem)
    fm_a = vault_common.parse_frontmatter(content_a)
    fm_b = vault_common.parse_frontmatter(content_b)
    tags_a = _parse_tags_list(fm_a)
    tags_b = _parse_tags_list(fm_b)
    body_a = vault_common.get_body(content_a).strip()
    body_b = vault_common.get_body(content_b).strip()

    print("=" * 60)
    print(f"NOTE A:  {path_a}")
    print(f"  Title:  {title_a}")
    print(f"  Tags:   {', '.join(tags_a) or '(none)'}")
    print(f"  Type:   {fm_a.get('type', '(none)')}")
    print(f"  Lines:  {len(body_a.splitlines())}")
    print()
    print(f"NOTE B:  {path_b}")
    print(f"  Title:  {title_b}")
    print(f"  Tags:   {', '.join(tags_b) or '(none)'}")
    print(f"  Type:   {fm_b.get('type', '(none)')}")
    print(f"  Lines:  {len(body_b.splitlines())}")
    print("=" * 60)
    print()
    # Preview first 5 lines of each body
    print("--- Note A preview ---")
    for line in body_a.splitlines()[:5]:
        print(f"  {line}")
    print()
    print("--- Note B preview ---")
    for line in body_b.splitlines()[:5]:
        print(f"  {line}")
    print()
