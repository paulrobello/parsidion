"""Interrupted links-phase replay must not produce a second vault diff."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Plain pytest runs lack the entry shim's anyio dependency; summarizer.pipeline
# only references it in an annotation. Same stub pattern as
# test_summarize_sessions._fresh_summarize_sessions.
_stubbed_anyio = (
    "anyio" not in sys.modules and importlib.util.find_spec("anyio") is None
)
if _stubbed_anyio:
    sys.modules["anyio"] = types.SimpleNamespace(  # type: ignore[assignment]
        Semaphore=object,
        to_thread=types.SimpleNamespace(run_sync=lambda func, *args: func(*args)),
    )

from core import vault_links  # noqa: E402
from core.vault_index import parse_frontmatter  # noqa: E402
from summarizer.pipeline import _apply_backlinks_and_strip_links  # noqa: E402

if _stubbed_anyio:
    del sys.modules["anyio"]


def _snapshot(vault: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(vault)): path.read_bytes()
        for path in vault.rglob("*")
        if path.is_file()
    }


def test_links_phase_replay_after_interruption_is_byte_identical(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay the production post-write stage without writing the note again.

    No embeddings DB is created, so semantic lookup takes its production empty-DB
    exit and the real tag fallback selects the same linked notes on each pass.
    This models a kill after link writes but before queue acknowledgement, not
    arbitrary interruption within an atomic write or a rerun of AI generation.
    """
    folder = tmp_vault / "Patterns"
    folder.mkdir()
    notes = [folder / f"note-{suffix}.md" for suffix in ("a", "b", "c")]
    for index, path in enumerate(notes):
        related = f'"[[{notes[(index + 1) % len(notes)].stem}]]"'
        if index == 0:
            related += ', "[[invented-hub]]"'
        path.write_text(
            "---\ndate: 2026-10-25\ntype: pattern\ntags: [link, replay]\n"
            f"related: [{related}]\n---\n# {path.stem}\n"
            + ("See [[invented-body|display text]].\n" if index == 0 else ""),
            encoding="utf-8",
        )

    strip_counts: list[int] = []
    real_strip = vault_links.strip_unresolved_wikilinks

    def recording_strip(content: str, vault: Path) -> tuple[str, int]:
        result, count = real_strip(content, vault)
        strip_counts.append(count)
        return result, count

    monkeypatch.setattr(vault_links, "strip_unresolved_wikilinks", recording_strip)
    written = notes[0]
    _apply_backlinks_and_strip_links(written, tmp_vault, notes)
    assert strip_counts == [2]
    first = _snapshot(tmp_vault)
    first_related = {
        path.name: parse_frontmatter(path.read_text(encoding="utf-8"))["related"]
        for path in notes
    }
    assert first_related["note-a.md"] == ["[[note-b]]", "[[note-c]]"]
    assert all("[[note-a]]" in first_related[path.name] for path in notes[1:])

    _apply_backlinks_and_strip_links(written, tmp_vault, notes)

    assert strip_counts == [2, 0]
    assert _snapshot(tmp_vault) == first
    for path in notes:
        content = path.read_text(encoding="utf-8")
        related = parse_frontmatter(content)["related"]
        assert related == first_related[path.name]
        assert len(related) == len(set(related))
        assert sum(line.startswith("related:") for line in content.splitlines()) == 1
