#!/usr/bin/env python3
"""Interactive curses-based TUI for vault search.

Extracted from vault_search.py to avoid eagerly importing curses and fastembed
when only metadata or grep modes are needed.

Launch directly:
    python vault_tui.py [--vault PATH]

Or via vault-search:
    vault-search --interactive

ARC-011: deviation from ARC-004's "library code lives in ``core/``" split,
documented rather than migrated. ``vault_tui.py`` is a CLI entrypoint that
imports ``curses`` at module load time and runs an interactive terminal
session; it is not a library module other code imports from. Moving it to
``core/vault_tui.py`` would require either a flat shim that re-exports
``main`` (over-engineering for a single-file CLI with no external
importers — only ``vault_search.py`` lazily imports it inside its
``--interactive`` branch) or moving the curses import inside the
entrypoint function (defeating the original extraction's purpose, which
was to keep the curses import out of ``vault_search.py``'s module load).
The other CLI tools (``vault_search.py``, ``vault_stats.py``,
``vault_conflicts.py``, ``vault_review.py``, ``vault_export.py``,
``vault_merge.py``) likewise keep their CLI entrypoints at the scripts
root rather than under ``core/``. The stdlib-only constraint applies
here exactly as it does to ``core/*`` — verified by
``tests/test_stdlib_only.py``.
"""

from __future__ import annotations

import argparse
import curses
import os
import subprocess as _sp
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import parmem_backend
import vault_common


# ---------------------------------------------------------------------------
# Shared curses list-view loop (ARC-013)
# ---------------------------------------------------------------------------


def run_list_view(
    stdscr: Any,
    rows: list[Any],
    render_row: Callable[[Any, Any, int, bool, int], None],
    on_key: Callable[[int, int], str | int | None],
    *,
    title: str | Callable[[int], str] = "",
    footer_keys: str = "",
    status: list[str] | None = None,
) -> int:
    """Run the shared curses list-view loop (ARC-013).

    Owns everything the three historical TUI loops duplicated: curses setup
    (``curs_set(0)``, keypad), the event loop, selected/scroll bookkeeping
    against the window height (resize-safe), the header/status bars, and
    ``j``/``k``/arrow navigation. Callers supply only the per-row renderer
    and the key handler.

    Args:
        stdscr: Window provided by ``curses.wrapper``.
        rows: Live list of items; the caller may mutate it (append/pop) from
            ``on_key`` — the selection is re-clamped against ``len(rows)``
            every frame and an emptied list exits the loop.
        render_row: ``(stdscr, row, y, is_selected, width)`` — draw one row
            at line *y*. Render nothing for rows that should stay blank.
        on_key: ``(key, selected)`` handler for every non-navigation key.
            Return ``"quit"`` to exit, an ``int`` to set the selection
            (clamped by the base), or ``None``/``"redraw"`` to continue.
        title: Header text, or a callable ``(selected) -> str`` re-evaluated
            each frame.
        footer_keys: Footer text shown when no status message is set.
        status: Optional single-cell mutable holder (``["msg"]``) shown in
            the footer for exactly one frame, then cleared — the vault-review
            action-feedback behaviour.

    Returns:
        The final selection index.
    """
    curses.curs_set(0)
    stdscr.keypad(True)

    selected = 0
    scroll = 0
    while rows:
        selected = max(0, min(selected, len(rows) - 1))
        h, w = stdscr.getmaxyx()
        list_height = h - 2  # header + footer

        if selected < scroll:
            scroll = selected
        elif selected >= scroll + list_height:
            scroll = selected - list_height + 1

        stdscr.clear()
        header_text = title(selected) if callable(title) else title
        header = header_text[: w - 1].ljust(w - 1)
        stdscr.attron(curses.A_REVERSE)
        stdscr.addstr(0, 0, header)
        stdscr.attroff(curses.A_REVERSE)

        for row_i in range(list_height):
            idx = scroll + row_i
            y = row_i + 1  # offset for the header line
            if idx >= len(rows):
                stdscr.move(y, 0)
                stdscr.clrtoeol()
                continue
            try:
                render_row(stdscr, rows[idx], y, idx == selected, w)
            except curses.error:
                pass

        msg = status[0] if status else ""
        footer = (msg or footer_keys)[: w - 1].ljust(w - 1)
        stdscr.attron(curses.A_REVERSE)
        try:
            stdscr.addstr(h - 1, 0, footer)
        except curses.error:
            pass
        stdscr.attroff(curses.A_REVERSE)
        if status:
            status[0] = ""
        stdscr.refresh()

        key = stdscr.getch()
        if key in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
            continue
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(len(rows) - 1, selected + 1)
            continue
        result = on_key(key, selected)
        if result == "quit":
            break
        if isinstance(result, int):
            selected = result
    return selected


def _search_notes(
    q: str, vault: Path, backend: str | None = None
) -> list[dict[str, object]]:
    """Run a search and return results.

    Tries semantic search first (via vault_search.search), then falls back
    to a title-substring scan over vault notes.

    Args:
        q: The user's query string.
        vault: Vault root path.
        backend: ``auto | par-mem | embeddings | none`` override; None reads
            the ``search.backend`` config key (default ``auto``).

    Returns:
        List of result dicts (max 10).
    """
    if not q.strip():
        return []
    db_path = vault_common.get_embeddings_db_path(vault)
    if db_path.exists() or parmem_backend.resolve_parmem_backend(vault):
        try:
            # Lazy import to avoid pulling fastembed at module level
            import vault_search  # noqa: PLC0415

            return vault_search.search(
                query=q, top=10, min_score=0.45, vault=vault, backend=backend
            )
        except Exception as exc:  # noqa: BLE001
            print(f"semantic search best-effort: {exc}", file=sys.stderr)
            pass
    # Fallback: metadata title search via grep over all notes
    matched: list[dict[str, object]] = []
    q_lower = q.lower()
    for note_path in vault_common.all_vault_notes(vault)[:200]:
        if q_lower in note_path.stem.lower():
            try:
                content = note_path.read_text(encoding="utf-8")
            except OSError:
                continue
            fm = vault_common.parse_frontmatter(content)
            title = vault_common.extract_title(content, note_path.stem)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            matched.append(
                {
                    "score": None,
                    "stem": note_path.stem,
                    "title": title,
                    "folder": note_path.parent.name
                    if note_path.parent != vault
                    else "",
                    "tags": tags,
                    "path": str(note_path),
                }
            )
    return matched[:10]


def _open_note(path_str: str) -> None:
    """Open a note in $EDITOR.

    Args:
        path_str: Absolute path to the note file.
    """
    editor = os.environ.get("EDITOR", "nano")
    try:
        _sp.run([editor, path_str])
    except (OSError, KeyboardInterrupt):
        pass


def _render_result_row(
    stdscr: Any, r: dict[str, object], y: int, is_selected: bool, w: int, zebra: bool
) -> None:
    """Draw one interactive-search result row (ARC-013 extraction)."""
    stem = str(r.get("stem", ""))
    title = str(r.get("title", ""))
    folder = str(r.get("folder", ""))
    score = r.get("score")
    score_str = f"{float(score):.3f} " if isinstance(score, (int, float)) else "      "
    line = f"{score_str}{folder}/{stem} — {title}"
    attr = curses.A_REVERSE if is_selected else curses.A_NORMAL
    if curses.has_colors() and not is_selected:
        attr = curses.color_pair(1) if zebra else curses.A_NORMAL
    stdscr.addstr(y, 0, line[: w - 1], attr)


def interactive_search(vault: Path | None = None, backend: str | None = None) -> None:
    """Launch a curses-based interactive vault search TUI.

    Real-time search as you type. Arrow keys navigate results.
    Enter opens the selected note in $EDITOR. 'q' or Ctrl+C quits.
    Falls back to a simple line-input loop when curses is unavailable.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
        backend: ``auto | par-mem | embeddings | none`` override; None reads
            the ``search.backend`` config key (default ``auto``).
    """
    vault = vault or vault_common.resolve_vault()

    def _run_tui(stdscr: Any) -> None:
        curses.curs_set(1)
        curses.use_default_colors()
        if curses.has_colors():
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)

        query_buf: list[str] = []
        results: list[dict[str, object]] = []
        selected = 0
        last_query = ""

        while True:
            stdscr.clear()
            h, w = stdscr.getmaxyx()

            # Header
            header = (
                " vault-search interactive  [up/down navigate]  [Enter open]  [q quit] "
            )
            stdscr.addstr(
                0,
                0,
                header[: w - 1],
                curses.A_REVERSE if curses.has_colors() else curses.A_BOLD,
            )

            # Query line
            prompt = "Search: "
            q_str = "".join(query_buf)
            if h > 1:
                stdscr.addstr(1, 0, f"{prompt}{q_str}"[: max(0, w - 1)])

            # Results
            max_results = h - 4
            for i, r in enumerate(results[:max_results]):
                y = i + 3
                if y >= h - 1:
                    break
                _render_result_row(stdscr, r, y, i == selected, w, zebra=(i % 2 == 0))

            if not results and q_str and 3 < h - 1 and w > 3:
                stdscr.addstr(3, 2, "No results found."[: w - 3], curses.A_DIM)

            # Status bar
            status = f" {len(results)} result(s) " if results else " Type to search... "
            stdscr.addstr(h - 1, 0, status[: max(0, w - 1)], curses.A_DIM)

            # Reposition cursor
            if h > 1:
                cursor_col = min(len(prompt) + len(q_str), w - 1)
                stdscr.move(1, cursor_col)
            stdscr.refresh()

            # Re-search if query changed
            if q_str != last_query:
                last_query = q_str
                results = _search_notes(q_str, vault, backend=backend)
                selected = 0

            # Input handling
            ch = stdscr.getch()

            if ch in (ord("q"), 27):  # q or ESC
                break
            elif ch in (curses.KEY_UP, ord("k")):
                selected = max(0, selected - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                selected = min(len(results) - 1, selected + 1) if results else 0
            elif ch in (curses.KEY_ENTER, 10, 13):
                if results and 0 <= selected < len(results):
                    path = str(results[selected].get("path", ""))
                    if path:
                        curses.endwin()
                        _open_note(path)
                        # Re-init and restore the full mode set that
                        # curses.wrapper established, so arrow keys don't
                        # leak escape bytes into the search buffer.
                        stdscr = curses.initscr()
                        curses.noecho()
                        curses.cbreak()
                        stdscr.keypad(True)
                        curses.curs_set(1)
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if query_buf:
                    query_buf.pop()
            elif 32 <= ch < 127:
                query_buf.append(chr(ch))

    try:
        curses.wrapper(_run_tui)
    except curses.error:
        # Terminal doesn't support curses -- fall back to simple loop
        print(
            "Interactive mode (non-curses fallback -- type query, Enter to search, blank to quit)"
        )
        while True:
            try:
                q = input("Search: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                break
            results = _search_notes(q, vault, backend=backend)
            if not results:
                print("  No results.")
                continue
            for i, r in enumerate(results):
                stem = r.get("stem", "")
                title = r.get("title", "")
                folder = r.get("folder", "")
                print(f"  [{i}] {folder}/{stem} — {title}")
            try:
                choice = input("Open [number] or Enter to continue: ").strip()
                if choice.isdigit() and int(choice) < len(results):
                    _open_note(str(results[int(choice)].get("path", "")))
            except (EOFError, KeyboardInterrupt):
                break


def main() -> None:
    """CLI entry point for standalone vault TUI invocation."""
    parser = argparse.ArgumentParser(
        prog="vault-tui",
        description="Interactive curses-based TUI for vault search.",
    )
    parser.add_argument(
        "--vault",
        "-V",
        metavar="PATH|NAME",
        default=None,
        help="Vault path or named vault (default: ~/ParsidionVault, or legacy ~/ClaudeVault if it exists)",
    )
    args = parser.parse_args()
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    interactive_search(vault_path)


if __name__ == "__main__":
    main()
