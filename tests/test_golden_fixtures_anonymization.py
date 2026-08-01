"""ENH-008 Step 4 — golden-set anonymization gate.

The golden fixtures under ``tests/fixtures/prompts/golden/<prompt>/`` feed the
prompt eval harness. They are derived from real session transcripts and notes,
so they are treated as sensitive by default (the audit found session metadata
leaking through ``.bak`` files — SEC-104). This test scans every fixture for the
leakage signatures the plan names and fails on the first hit:

- absolute home paths (``/Users/<name>``, ``/home/<name>``)
- ``$HOME`` / ``~`` expansions
- hostnames (``<name>.local``, ``.internal``, common dev box patterns)
- common credential patterns (``API_KEY=``, ``TOKEN=``, ``Bearer ``, private keys)
- the maintainer's own usernames (``probello``, the dev box hostnames)

This is a content test, not a parsing test — a fixture that legitimately needs
a placeholder path uses ``/workspace/project`` or ``/vault/...`` forms that do
not match a real machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "prompts" / "golden"
# summarize-session's golden cases live in their own subdir (one per prompt).
# Its count + rubric-field checks are note-specific, so they target this dir.
_SUMMARIZE_SESSION_DIR = _FIXTURE_ROOT / "summarize-session"


# Leakage signatures. Each is a compiled regex; a match anywhere in any
# fixture file fails the test. Patterns are deliberately broad — a false
# positive here is far cheaper than a real leak.
_LEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Absolute home-directory paths (macOS + Linux).
    ("absolute home path", re.compile(r"/Users/[A-Za-z][\w.-]*")),
    ("absolute home path", re.compile(r"/home/[A-Za-z][\w.-]*")),
    ("Windows user path", re.compile(r"[A-Za-z]:\\Users\\")),
    # Shell home expansions.
    ("$HOME expansion", re.compile(r"\$HOME\b")),
    ("$USER expansion", re.compile(r"\$USER\b")),
    ("tilde home expansion", re.compile(r"~[/\"]")),
    # Hostnames / dev boxes that would identify a machine.
    (".local hostname", re.compile(r"[A-Za-z][\w-]*\.local\b")),
    (".internal hostname", re.compile(r"[A-Za-z][\w-]*\.internal\b")),
    # Common credential patterns (case-insensitive key/token assignments).
    (
        "API key assignment",
        re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd)\b\s*[=:]\s*\S"),
    ),
    (
        "token assignment",
        re.compile(r"(?i)\b(access[_-]?token|auth[_-]?token|bearer)\b\s*[=:]\s*\S"),
    ),
    ("AWS key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    (
        "private key header",
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    ),
    # Maintainer-specific identifiers (the real names this vault belongs to).
    # A golden fixture must never reference a specific person.
    ("maintainer username", re.compile(r"\bprobello\b", re.IGNORECASE)),
]


def _all_fixture_files() -> list[Path]:
    """Every fixture file across all per-prompt golden subdirs.

    The anonymization scan covers every prompt's fixtures — they are all
    derived from real session/note data.
    """
    if not _FIXTURE_ROOT.is_dir():
        return []
    return sorted(
        p
        for p in _FIXTURE_ROOT.rglob("*")
        if p.is_file() and p.suffix in (".md", ".yaml", ".yml")
    )


def _summarize_session_files() -> list[Path]:
    if not _SUMMARIZE_SESSION_DIR.is_dir():
        return []
    return sorted(p for p in _SUMMARIZE_SESSION_DIR.iterdir() if p.is_file())


def test_golden_directory_has_expected_case_count() -> None:
    """The plan calls for 8+ summarize-session cases. Fail if the set shrinks."""
    transcripts = [
        p for p in _summarize_session_files() if p.name.endswith(".transcript.md")
    ]
    assert 8 <= len(transcripts) <= 20, (
        f"expected 8-20 summarize-session transcripts, found {len(transcripts)}: "
        f"{[p.name for p in transcripts]}"
    )
    # Every transcript has a matching .expected.yaml.
    for t in transcripts:
        stem = t.name.removesuffix(".transcript.md")
        expected = _SUMMARIZE_SESSION_DIR / f"{stem}.expected.yaml"
        assert expected.is_file(), f"missing expected YAML for {t.name}: {expected}"


@pytest.mark.parametrize("fixture_path", _all_fixture_files())
def test_golden_fixture_is_anonymized(fixture_path: Path) -> None:
    """No fixture file contains a leakage signature."""
    text = fixture_path.read_text(encoding="utf-8")
    for label, pattern in _LEAK_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{fixture_path.relative_to(_FIXTURE_ROOT)}: {label} leakage at offset "
            f"{match.start() if match else '?'}: {match.group(0) if match else ''!r}"
        )


def test_golden_expected_yaml_has_required_fields() -> None:
    """Every summarize-session expected.yaml declares the note-rubric fields."""
    expected_files = [
        p for p in _summarize_session_files() if p.name.endswith(".expected.yaml")
    ]
    assert expected_files, "no summarize-session .expected.yaml files found"
    required = {
        "should_produce_note",
        "expected_type",
        "expected_tags_include",
        "expected_tags_exclude",
        "must_mention",
        "frontmatter_valid",
    }
    for ef in expected_files:
        text = ef.read_text(encoding="utf-8")
        missing = [k for k in required if not re.search(rf"^{k}:", text, re.MULTILINE)]
        assert not missing, f"{ef.name}: missing rubric fields {missing}"
