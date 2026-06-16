"""vault-conflicts: detect contradictions between semantically-similar vault notes.

Companion to vault-merge (which merges near-duplicates). Clusters notes by
embedding similarity, then asks the configured prompt AI backend whether any
pair in a cluster makes mutually-exclusive claims. Conflicts can be reviewed
interactively (curses TUI) or emitted as JSON.
"""

from __future__ import annotations

import json
import re
import struct  # noqa: F401
import sys  # noqa: F401
from collections import defaultdict  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any

import vault_common  # noqa: F401

_DEFAULT_TOPIC_THRESHOLD = 0.75
_DEFAULT_MAX_CLUSTER = 8
_DEFAULT_TOP = 50
_DEFAULT_AI_TIMEOUT = 90

_CONFLICTS_DIRNAME = "conflicts"
_CONFLICTS_FILENAME = "report.json"


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """Extract the first JSON array from *text*; return [] if none/unparseable.

    Tolerates markdown fences and surrounding prose from an LLM.
    """
    if not text:
        return []
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove a leading ```json / ``` fence and trailing fence.
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        parsed = json.loads(stripped[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def main() -> None:  # pragma: no cover - wired in Task 4.6
    """CLI entry point (implemented in Task 4.6)."""
    raise NotImplementedError


if __name__ == "__main__":
    main()
