"""Output formatters + env-var helpers (ARC-005).

Extracted from ``vault_search.py``. Two display formatters
(``_format_text`` returns a string, ``_format_rich`` prints) plus the
``_env_float`` / ``_env_int`` env-var readers used by ``main`` to wire
``VAULT_SEARCH_*`` overrides into argparse defaults.
"""

from __future__ import annotations

import os
from typing import Any

from cli.search._common import _ENV_PREFIX


def _format_text(results: list[dict[str, Any]]) -> str:
    """Format results as human-readable one-line-per-note text.

    Args:
        results: List of result dicts.

    Returns:
        Newline-separated string.
    """
    lines: list[str] = []
    for r in results:
        score = r.get("score")
        tags = r.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""
        stale = " [STALE]" if r.get("is_stale") else ""
        tags_label = f" [{tags_str}]" if tags_str else ""
        score_label = f"{float(score):.4f}  " if isinstance(score, (int, float)) else ""
        lines.append(
            f"{score_label}{r['folder'] or '.'}/{r['stem']}{tags_label}{stale} — {r['title']}"
        )
    return "\n".join(lines)


def _format_rich(results: list[dict[str, Any]]) -> None:
    """Print results with Rich colorized one-line-per-note output.

    Score is colored green (>=0.80), yellow (>=0.60), or red (<0.60).
    Folder is cyan, stem bold, tags dim yellow, title bright white.

    Args:
        results: List of result dicts.
    """
    from rich.console import Console
    from rich.text import Text

    console = Console()
    for r in results:
        score = r.get("score")
        tags = r.get("tags", [])
        tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else ""
        is_stale = bool(r.get("is_stale"))

        line = Text()

        if isinstance(score, (int, float)):
            s = float(score)
            score_style = (
                "bold green" if s >= 0.80 else "yellow" if s >= 0.60 else "red"
            )
            line.append(f"{s:.4f}  ", style=score_style)

        line.append(r.get("folder") or ".", style="cyan")
        line.append("/", style="dim white")
        line.append(str(r.get("stem", "")), style="bold white")

        if tags_str:
            line.append(" [", style="dim white")
            line.append(tags_str, style="dim yellow")
            line.append("]", style="dim white")

        if is_stale:
            line.append(" [STALE]", style="bold red")

        line.append(" — ", style="dim white")
        line.append(str(r.get("title", "")), style="bright_white")

        console.print(line, soft_wrap=True)


def _env_float(name: str, fallback: float) -> float:
    """Return float from env var *name* or *fallback* on missing/invalid value.

    Args:
        name: Environment variable name (without prefix).
        fallback: Value to use when the variable is absent or non-numeric.

    Returns:
        Parsed float or fallback.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def _env_int(name: str, fallback: int) -> int:
    """Return int from env var *name* or *fallback* on missing/invalid value.

    Args:
        name: Environment variable name (without prefix).
        fallback: Value to use when the variable is absent or non-integer.

    Returns:
        Parsed int or fallback.
    """
    raw = os.environ.get(_ENV_PREFIX + name)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback
