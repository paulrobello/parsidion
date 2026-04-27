#!/usr/bin/env python3
"""Interactive curses-based TUI for vault search.

Extracted from vault_search.py to avoid eagerly importing curses and fastembed
when only metadata or grep modes are needed.

Launch directly:
    python vault_tui.py [--vault PATH]

Or via vault-search:
    vault-search --interactive
"""

from __future__ import annotations

import argparse
import curses
import os
import subprocess as _sp
from pathlib import Path
from typing import Any

import vault_common


def _search_notes(q: str, vault: Path) -> list[dict[str, object]]:
    """Run a search and return results.

    Tries semantic search first (via vault_search.search), then falls back
    to a title-substring scan over vault notes.

    Args:
        q: The user's query string.
        vault: Vault root path.

    Returns:
        List of result dicts (max 10).
    """
    if not q.strip():
        return []
    db_path = vault_common.get_embeddings_db_path(vault)
    if db_path.exists():
        try:
            # Lazy import to avoid pulling fastembed at module level
            import vault_search  # noqa: PLC0415

            return vault_search.search(query=q, top=10, min_score=0.45, vault=vault)
        except Exception:  # noqa: BLE001
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


def interactive_search(vault: Path | None = None) -> None:
    """Launch a curses-based interactive vault search TUI.

    Real-time search as you type. Arrow keys navigate results.
    Enter opens the selected note in $EDITOR. 'q' or Ctrl+C quits.
    Falls back to a simple line-input loop when curses is unavailable.

    Args:
        vault: Optional vault path. Defaults to resolve_vault().
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
            stdscr.addstr(1, 0, f"{prompt}{q_str[: w - len(prompt) - 1]}")

            # Results
            max_results = h - 4
            for i, r in enumerate(results[:max_results]):
                y = i + 3
                if y >= h - 1:
                    break
                stem = str(r.get("stem", ""))
                title = str(r.get("title", ""))
                folder = str(r.get("folder", ""))
                score = r.get("score")
                score_str = (
                    f"{float(score):.3f} "
                    if isinstance(score, (int, float))
                    else "      "
                )
                line = f"{score_str}{folder}/{stem} — {title}"
                attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
                if curses.has_colors() and i != selected:
                    attr = curses.color_pair(1) if i % 2 == 0 else curses.A_NORMAL
                stdscr.addstr(y, 0, line[: w - 1], attr)

            if not results and q_str:
                stdscr.addstr(3, 2, "No results found.", curses.A_DIM)

            # Status bar
            status = f" {len(results)} result(s) " if results else " Type to search... "
            stdscr.addstr(h - 1, 0, status[: w - 1], curses.A_DIM)

            # Reposition cursor
            cursor_col = min(len(prompt) + len(q_str), w - 1)
            stdscr.move(1, cursor_col)
            stdscr.refresh()

            # Re-search if query changed
            if q_str != last_query:
                last_query = q_str
                results = _search_notes(q_str, vault)
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
                        stdscr = curses.initscr()
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
            results = _search_notes(q, vault)
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
        help="Vault path or named vault (default: ~/ClaudeVault)",
    )
    args = parser.parse_args()
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())
    interactive_search(vault_path)


if __name__ == "__main__":
    main()
