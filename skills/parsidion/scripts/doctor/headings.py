"""Python-only heading and self-reference fixers (no Claude call).

Extracted from the original ``vault_doctor.py`` (ARC-008 / QA-003).

Stdlib-only.
"""

from __future__ import annotations

import re
from pathlib import Path

import vault_common
import vault_fs

from doctor._state import _active_vault, _backup_note


def _auto_fix_self_refs(path: Path) -> bool:
    """Remove self-referencing wikilinks from the ``related`` frontmatter field.

    Returns True if the file was modified.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return False

    stem = path.stem
    self_ref = f"[[{stem}]]"
    related_re = re.compile(r"^(related:\s*)(\[.*?\])\s*$", re.MULTILINE)
    m = related_re.search(content)
    if not m:
        return False

    prefix = m.group(1)
    raw_list = m.group(2)
    entries = re.findall(r'"(\[\[[^\]]+\]\])"', raw_list)
    if not entries:
        return False

    filtered = [e for e in entries if e != self_ref]
    if len(filtered) == len(entries):
        return False

    if filtered:
        quoted = ", ".join(f'"{e}"' for e in filtered)
        new_related_line = f"{prefix}[{quoted}]"
    else:
        new_related_line = f"{prefix}[]"

    updated = related_re.sub(new_related_line, content)
    if updated == content:
        return False

    _backup_note(_active_vault(), path)
    vault_fs.atomic_write_text(path, updated)
    return True


def _auto_fix_headings(path: Path) -> bool:
    """Promote the first ``## `` heading to ``# `` when no ``# `` heading exists.

    Returns True if the file was modified.
    """
    content = path.read_text(encoding="utf-8")
    body = vault_common.get_body(content)

    # Check there is no existing # heading
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return False  # already has a proper H1

    # Find and promote the first ## heading
    lines = content.split("\n")
    modified = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## ") and not stripped.startswith("### "):
            # Only promote if we're past the frontmatter
            lines[i] = line.replace("## ", "# ", 1)
            modified = True
            break

    if modified:
        _backup_note(_active_vault(), path)
        vault_fs.atomic_write_text(path, "\n".join(lines))
    return modified
