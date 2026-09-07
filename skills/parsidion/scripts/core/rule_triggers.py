"""Rule-note discovery and matching for ``type: rule`` vault notes.

A ``type: rule`` note carries a ``triggers`` frontmatter list — kebab-case
keywords and fnmatch path patterns, with ``[always]`` as the always-on
sentinel (syntax contract in ``note_schema``). The prompt-submit and
pre-tool-use hooks inject a rule only when one of its triggers fires for the
current prompt or file path: rules are pushed when they apply and never
injected otherwise.

Discovery is index-first with a frontmatter-walk fallback so rules push even
when the note_index is missing. Index rows are live by construction (the
index build excludes superseded notes); the walk fallback re-filters
superseded notes explicitly.

SEC-005/SEC-130: index rows carry DB-sourced ``path`` strings, so every file
read is re-validated for vault containment first.

Stdlib-only — transitively imported by hooks; covered by
tests/test_stdlib_only.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import note_schema
from core.vault_index import (
    all_vault_notes,
    extract_title,
    load_note_index_metadata,
    parse_frontmatter,
)
from core.vault_path import is_path_inside_vault

__all__ = ["load_rule_notes", "match_rules"]


def load_rule_notes(vault: str | Path) -> list[dict[str, Any]]:
    """Live rule notes with parsed triggers (index mtime order).

    Index-first: ``load_note_index_metadata`` rows filtered to
    ``note_type == "rule"``. The walk fallback runs only when the index
    DB/table is missing (``None``), so a live index that predates a
    just-written rule note behaves like every other index consumer: the rule
    appears after the next index rebuild.
    """
    v = Path(vault)
    rows = load_note_index_metadata(vault=v)
    if rows is not None:
        vault_resolved = v.resolve()
        rules: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("note_type") != "rule":
                continue
            rule = _rule_from_path(
                Path(str(row.get("path") or "")),
                vault=vault_resolved,
                fallback=row,
            )
            if rule is not None:
                rules.append(rule)
        return rules
    return _rules_from_walk(v)


def _rule_from_path(
    p: Path,
    vault: Path,
    fallback: dict[str, Any],
) -> dict[str, Any] | None:
    """Rule dict from one candidate path, or None when unusable.

    Containment re-validation (SEC-005/SEC-130): the DB-sourced path must be
    inside *vault* before any read.
    """
    try:
        if not p.exists() or not is_path_inside_vault(p.resolve(), vault):
            return None
        content = p.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if fm.get("type") != "rule":
        return None
    if fm.get("status") == note_schema.STATUS_SUPERSEDED:
        return None
    triggers = note_schema.parse_triggers(fm)
    if not triggers:
        return None
    return {
        "path": str(p),
        "title": str(fm.get("title") or fallback.get("title") or p.stem),
        "stem": p.stem,
        "folder": p.parent.name,
        "summary": str(fallback.get("summary") or ""),
        "triggers": triggers,
    }


def _rules_from_walk(vault: Path) -> list[dict[str, Any]]:
    """Rules by parsing frontmatter over the full vault walk (no index).

    Superseded rules are excluded here because the index-first path relies
    on the index build to exclude them.
    """
    rules: list[dict[str, Any]] = []
    for p in all_vault_notes(vault):
        try:
            content = p.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if (
            fm.get("type") != "rule"
            or fm.get("status") == note_schema.STATUS_SUPERSEDED
        ):
            continue
        triggers = note_schema.parse_triggers(fm)
        if not triggers:
            continue
        rules.append(
            {
                "path": str(p),
                "title": str(fm.get("title") or extract_title(content, p.stem)),
                "stem": p.stem,
                "folder": p.parent.name,
                "summary": "",
                "triggers": triggers,
            }
        )
    return rules


def match_rules(
    rules: list[dict[str, Any]],
    prompt: str | None = None,
    path: str | None = None,
) -> list[dict[str, Any]]:
    """Rules with at least one firing trigger; each carries ``matched``.

    *matched* lists the trigger strings that fired (surfaced in the
    injection for transparency). Give exactly one of *prompt*/*path*.
    """
    matched: list[dict[str, Any]] = []
    for rule in rules:
        hits = [
            t
            for t in rule.get("triggers", [])
            if note_schema.match_trigger(t, prompt=prompt, path=path)
        ]
        if hits:
            entry = dict(rule)
            entry["matched"] = hits
            matched.append(entry)
    return matched
