#!/usr/bin/env python3
"""Fake vault_search.py — happy path (test fixture)."""

import json
import sys


def flag_value(flag: str, default: str = "") -> str:
    argv = sys.argv[1:]
    return argv[argv.index(flag) + 1] if flag in argv else default


vault = flag_value("--vault", "/tmp/vault")
query = sys.argv[-1]  # positional query is always the final argument

rows = [
    {
        "score": 0.0441,
        "stem": "note-alpha",
        "title": "Note Alpha",
        "folder": "Patterns",
        "tags": ["a", "b"],
        "path": f"{vault}/Patterns/note-alpha.md",
        "summary": query,
        "note_type": "pattern",
        "project": "",
        "confidence": "high",
        "mtime": 0.0,
        "related": [],
        "is_stale": False,
        "incoming_links": 0,
    },
    {
        "score": 0.02,
        "stem": "note-beta",
        "title": "Note Beta",
        "folder": "Debugging",
        "tags": [],
        "path": f"{vault}/Debugging/note-beta.md",
        "summary": "Second fake note.",
        "note_type": "debugging",
        "project": "",
        "confidence": "high",
        "mtime": 0.0,
        "related": [],
        "is_stale": False,
        "incoming_links": 0,
    },
]
print(json.dumps(rows))
