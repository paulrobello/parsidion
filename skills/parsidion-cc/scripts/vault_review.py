#!/usr/bin/env python3
"""vault-review — curses TUI for reviewing pending sessions in pending_summaries.jsonl.

Modes:
    (no flag)       Launch interactive curses TUI
    --list          Print pending sessions without TUI
    --clear         Remove all entries from queue (with confirmation)

Key bindings (TUI):
    j / Down        Move selection down
    k / Up          Move selection up
    d               Dump transcript excerpt (first 20 lines)
    y               Approve entry (adds "status": "approved")
    n               Reject entry (removes from queue)
    s               Skip entry (no change)
    q               Quit
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# These scripts are not a proper package — sys.path.insert is intentional so
# each script can run standalone via ``uv run`` or ``python`` without requiring
# pip install or editable installs.
sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

_PENDING_PATH: Path = vault_common.VAULT_ROOT / "pending_summaries.jsonl"
_EXCERPT_LINES: int = 20


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def _read_entries() -> list[dict]:
    """Read all entries from pending_summaries.jsonl.

    Returns:
        List of parsed JSON objects; empty list if file is absent.
    """
    if not _PENDING_PATH.exists():
        return []
    entries: list[dict] = []
    with open(_PENDING_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _write_entries(entries: list[dict]) -> None:
    """Atomically write entries back to pending_summaries.jsonl.

    Args:
        entries: List of JSON-serialisable dicts to persist.
    """
    tmp = _PENDING_PATH.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        vault_common.flock_exclusive(fh, vault_path=vault_path)
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")
    tmp.replace(_PENDING_PATH)


def _fmt_timestamp(ts: str) -> str:
    """Format an ISO timestamp to a short human-readable string.

    Args:
        ts: ISO-8601 timestamp string, possibly with fractional seconds.

    Returns:
        Short datetime string like ``2026-03-17 14:05``, or original on error.
    """
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ts or "unknown"


def _entry_summary(entry: dict) -> str:
    """Build a one-line summary for an entry.

    Args:
        entry: A parsed pending_summaries entry dict.

    Returns:
        Human-readable summary line.
    """
    ts = _fmt_timestamp(entry.get("timestamp", ""))
    project = entry.get("project", "(none)")
    source = entry.get("source", "session")
    agent_type = entry.get("agent_type", "")
    source_label = f"{source}/{agent_type}" if agent_type else source
    status = entry.get("status", "")
    status_suffix = f" [{status}]" if status else ""
    cats = entry.get("categories", {})
    cat_names = list(cats.keys()) if isinstance(cats, dict) else []
    cat_str = ", ".join(cat_names[:3]) if cat_names else "—"
    return f"{ts}  {project:<20}  {source_label:<14}  {cat_str}{status_suffix}"


def _resolve_transcript_path(entry: dict) -> Path | None:
    """Return the best available Path for an entry's transcript.

    Tries the stored ``transcript_path`` first, then applies known fallbacks
    for older entries where the path was stored without the ``agent-`` prefix
    that Claude Code uses for subagent transcript filenames.

    Args:
        entry: Pending summary entry dict.

    Returns:
        Resolved Path if a readable file is found, else None.
    """
    raw = entry.get("transcript_path", "") or entry.get("agent_transcript_path", "")
    if not raw:
        return None
    path = Path(raw)
    if path.exists():
        return path
    # Fallback: Claude Code stores subagent transcripts as agent-<id>.jsonl but
    # older hook versions stored the path without the "agent-" prefix.
    candidate = path.parent / f"agent-{path.stem}.jsonl"
    if candidate.exists():
        return candidate
    return None


def _read_transcript_excerpt(entry: dict, n: int = _EXCERPT_LINES) -> list[str]:
    """Read the first n text-bearing lines from the transcript.

    Args:
        entry: Pending summary entry dict containing ``transcript_path``.
        n: Number of lines to extract.

    Returns:
        List of text lines from the transcript.
    """
    path = _resolve_transcript_path(entry)
    if path is None:
        raw = entry.get("transcript_path", "")
        return [
            f"Transcript not found: {raw or '(no path)'}",
            "",
            "This can happen when:",
            "  • Claude Code cleaned up old transcripts",
            "  • The entry was queued before a bug fix (re-queue by running a new session)",
        ]

    lines: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Extract human-readable text from transcript events.
                # Subagent transcripts nest content under obj["message"]["content"];
                # session transcripts use obj["content"] directly.
                if isinstance(obj, dict):
                    msg = obj.get("message")
                    if isinstance(msg, dict):
                        raw_content = msg.get("content", "")
                    else:
                        raw_content = obj.get("content", "")
                    text = vault_common.extract_text_from_content(raw_content, vault_path=vault_path)
                    if text:
                        for sub in text.splitlines():
                            lines.append(sub[:200])
                            if len(lines) >= n:
                                break
                    if len(lines) >= n:
                        break
    except OSError as exc:
        return [f"(error reading transcript: {exc})"]
    return lines if lines else ["(no readable content in transcript)"]


# ---------------------------------------------------------------------------
# --list mode
# ---------------------------------------------------------------------------


def _cmd_list() -> None:
    """Print pending sessions to stdout without launching the TUI."""
    entries = _read_entries()
    if not entries:
        print("No pending sessions.")
        return
    print(f"Pending sessions: {len(entries)}\n")
    for i, entry in enumerate(entries, 1):
        status = entry.get("status", "")
        status_suffix = f"  [{status}]" if status else ""
        print(f"  {i:>3}.  {_entry_summary(entry)}{status_suffix}")


# ---------------------------------------------------------------------------
# --clear mode
# ---------------------------------------------------------------------------


def _cmd_clear() -> None:
    """Remove all entries from the queue after confirmation."""
    entries = _read_entries()
    if not entries:
        print("Queue is already empty.")
        return
    answer = input(f"Remove all {len(entries)} pending entries? [y/N] ").strip().lower()
    if answer != "y":
        print("Cancelled.")
        return
    _write_entries([])
    print("Queue cleared.")


# ---------------------------------------------------------------------------
# TUI helpers
# ---------------------------------------------------------------------------


def _draw_header(stdscr, title: str) -> None:
    """Draw a header bar at the top of the screen.

    Args:
        stdscr: The curses window.
        title: Text to display in the header.
    """
    import curses

    h, w = stdscr.getmaxyx()
    header = title[: w - 1].ljust(w - 1)
    stdscr.attron(curses.A_REVERSE)
    stdscr.addstr(0, 0, header)
    stdscr.attroff(curses.A_REVERSE)


def _draw_footer(stdscr, msg: str = "") -> None:
    """Draw a footer bar with key bindings at the bottom of the screen.

    Args:
        stdscr: The curses window.
        msg: Optional status message to display.
    """
    import curses

    h, w = stdscr.getmaxyx()
    keys = "j/k:nav  Enter/d:dump(y/n inside)  y:approve  n:reject  s:skip  q:quit"
    footer = (msg or keys)[: w - 1].ljust(w - 1)
    stdscr.attron(curses.A_REVERSE)
    try:
        stdscr.addstr(h - 1, 0, footer)
    except curses.error:
        pass
    stdscr.attroff(curses.A_REVERSE)


def _draw_list(stdscr, entries: list[dict], selected: int, scroll: int) -> None:
    """Render the entry list in the main area of the screen.

    Args:
        stdscr: The curses window.
        entries: Current list of pending entries.
        selected: Index of the currently selected entry.
        scroll: Vertical scroll offset (first visible entry index).
    """
    import curses

    h, w = stdscr.getmaxyx()
    list_height = h - 2  # header + footer
    for row in range(list_height):
        idx = scroll + row
        y = row + 1  # offset for header
        if idx >= len(entries):
            stdscr.move(y, 0)
            stdscr.clrtoeol()
            continue
        entry = entries[idx]
        status = entry.get("status", "")
        prefix = {
            "approved": "[A] ",
            "rejected": "[R] ",
        }.get(status, "    ")
        line = (prefix + _entry_summary(entry))[: w - 1]
        line = line.ljust(w - 1)
        attr = curses.A_REVERSE if idx == selected else curses.A_NORMAL
        if status == "approved":
            attr |= curses.A_BOLD
        try:
            stdscr.addstr(y, 0, line, attr)
        except curses.error:
            pass


def _show_popup(stdscr, lines: list[str], title: str = "") -> int:
    """Display a scrollable popup overlay with the given lines.

    Returns the key that closed the popup so the caller can act on y/n presses.

    Args:
        stdscr: The curses window.
        lines: Lines of text to display.
        title: Optional title shown in the popup border.

    Returns:
        The integer key code that closed the popup.
    """
    import curses

    h, w = stdscr.getmaxyx()
    pop_h = min(h - 4, len(lines) + 4)
    pop_w = min(w - 4, 100)
    top = (h - pop_h) // 2
    left = (w - pop_w) // 2

    win = curses.newwin(pop_h, pop_w, top, left)
    win.keypad(True)
    win.box()
    if title:
        win.addstr(0, 2, f" {title[: pop_w - 6]} ")

    inner_h = pop_h - 2
    inner_w = pop_w - 4
    offset = 0
    closing_key = ord("q")
    while True:
        win.clear()
        win.box()
        if title:
            try:
                win.addstr(0, 2, f" {title[: pop_w - 6]} ")
            except curses.error:
                pass
        for i in range(inner_h):
            line_idx = offset + i
            if line_idx >= len(lines):
                break
            text = lines[line_idx][:inner_w]
            try:
                win.addstr(i + 1, 2, text)
            except curses.error:
                pass
        more = "[↑↓:scroll  y:approve  n:reject  any other key:close]"
        try:
            win.addstr(pop_h - 1, 2, more[: pop_w - 4])
        except curses.error:
            pass
        win.refresh()
        key = win.getch()
        if key in (curses.KEY_DOWN, ord("j")):
            if offset + inner_h < len(lines):
                offset += 1
        elif key in (curses.KEY_UP, ord("k")):
            if offset > 0:
                offset -= 1
        else:
            closing_key = key
            break
    del win
    stdscr.touchwin()
    stdscr.refresh()
    return closing_key


# ---------------------------------------------------------------------------
# Main TUI loop
# ---------------------------------------------------------------------------


def _run_tui(stdscr) -> None:
    """Main curses event loop for the review TUI.

    Args:
        stdscr: The curses window provided by ``curses.wrapper``.
    """
    import curses

    curses.curs_set(0)
    stdscr.keypad(True)

    entries = _read_entries()
    if not entries:
        stdscr.clear()
        stdscr.addstr(1, 2, "No pending sessions in queue.")
        stdscr.addstr(2, 2, "Press any key to exit.")
        stdscr.refresh()
        stdscr.getch()
        return

    selected = 0
    scroll = 0
    status_msg = ""

    while True:
        h, w = stdscr.getmaxyx()
        list_height = h - 2

        # Keep scroll in sync with selected
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + list_height:
            scroll = selected - list_height + 1

        stdscr.clear()
        _draw_header(stdscr, f"Vault Review — {len(entries)} pending sessions")
        _draw_list(stdscr, entries, selected, scroll)
        _draw_footer(stdscr, status_msg)
        status_msg = ""
        stdscr.refresh()

        key = stdscr.getch()

        # Navigation
        if key in (curses.KEY_DOWN, ord("j")):
            selected = min(selected + 1, len(entries) - 1)

        elif key in (curses.KEY_UP, ord("k")):
            selected = max(selected - 1, 0)

        # Dump transcript excerpt
        elif key in (ord("d"), ord("\n"), curses.KEY_ENTER, 10, 13):
            while True:
                entry = entries[selected]
                excerpt = _read_transcript_excerpt(entry)
                closing = _show_popup(stdscr, excerpt, title="Transcript Excerpt")

                if closing == ord("y"):
                    entries[selected]["status"] = "approved"
                    _write_entries(entries)
                    status_msg = f"Entry {selected + 1} approved."
                    selected = min(selected + 1, len(entries) - 1)
                    if selected >= len(entries):
                        break
                elif closing == ord("n"):
                    entries.pop(selected)
                    _write_entries(entries)
                    if not entries:
                        break
                    selected = min(selected, len(entries) - 1)
                    status_msg = "Entry removed from queue."
                else:
                    break  # any other key just closes the popup

                # After y/n: show next entry's transcript automatically
                if entries:
                    continue
                break

        # Approve
        elif key == ord("y"):
            entries[selected]["status"] = "approved"
            _write_entries(entries)
            status_msg = f"Entry {selected + 1} approved."
            selected = min(selected + 1, len(entries) - 1)

        # Reject (remove from queue)
        elif key == ord("n"):
            entries.pop(selected)
            _write_entries(entries)
            if not entries:
                break
            selected = min(selected, len(entries) - 1)
            status_msg = "Entry removed from queue."

        # Skip
        elif key == ord("s"):
            selected = min(selected + 1, len(entries) - 1)
            status_msg = "Skipped."

        # Quit
        elif key in (ord("q"), 27):  # q or ESC
            break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI arguments and dispatch to the appropriate command.

    Raises:
        SystemExit: On invalid arguments or after completion.
    """
    parser = argparse.ArgumentParser(
        prog="vault-review",
        description="Review pending sessions in pending_summaries.jsonl.",
    )
    parser.add_argument(
        "--vault",
        "-V",
        metavar="VAULT",
        default=None,
        help="Use a specific vault (path or named vault).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--list",
        action="store_true",
        help="Print pending sessions without launching the TUI.",
    )
    group.add_argument(
        "--clear",
        action="store_true",
        help="Remove all entries from the queue (with confirmation).",
    )
    args = parser.parse_args()

    # Resolve vault path
    vault_path = vault_common.resolve_vault(explicit=args.vault, cwd=os.getcwd())

    # Replace module-level VAULT_ROOT with resolved vault path
    vault_common.VAULT_ROOT = vault_path

    try:
        if args.list:
            _cmd_list()
            return

        if args.clear:
            _cmd_clear()
            return

        # Auto-migrate on every startup (silent — fixes old entries in-place)
        vault_common.migrate_pending_paths(dry_run=False, vault_path=vault_path)

        # Check for pending sessions before attempting curses
        entries = _read_entries()
        if not entries:
            print("No pending sessions.")
            return

        # Try curses; fall back to --list mode if terminal doesn't support it
        try:
            import curses

            curses.wrapper(_run_tui)
        except Exception:  # noqa: BLE001
            print(
                "Warning: terminal does not support curses, falling back to --list mode.",
                file=sys.stderr,
            )
            _cmd_list()

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
