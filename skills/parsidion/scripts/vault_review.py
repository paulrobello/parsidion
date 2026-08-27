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
import os
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import vault_common


def _pending_path() -> Path:
    """Return the pending summaries path resolved against the current VAULT_ROOT.

    ARC-005: call-time resolution ensures that monkey-patches to
    ``vault_common.VAULT_ROOT`` (ARC-001) are reflected correctly instead of
    baking the path at import time.
    """
    return vault_common.VAULT_ROOT / "pending_summaries.jsonl"


_EXCERPT_LINES: int = 20


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------


def _read_entries() -> list[dict]:
    """Read all entries from pending_summaries.jsonl.

    Returns:
        List of parsed JSON objects; empty list if file is absent.
    """
    pp = _pending_path()
    if not pp.exists():
        return []
    entries: list[dict] = []
    with open(pp, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


def _write_entries(entries: list[dict], vault_path: Path | None = None) -> None:
    """Atomically write entries back to pending_summaries.jsonl.

    SEC-126: the lock must be on the REAL queue file so concurrent
    writers (session_stop_hook, subagent_stop_hook) cannot interleave.
    The previous implementation locked only the private ``.jsonl.tmp``
    file, which excluded nobody — the lock was effectively a no-op.

    The lock is held across the tmp-write + atomic replace. The replace
    itself is atomic on POSIX; the lock protects the read-modify-write
    window against a concurrent appender that would otherwise be lost.

    SEC-014: the tmp file is created 0600 so a world-readable tmp never
    sits beside the 0600 queue.

    Args:
        entries: List of JSON-serialisable dicts to persist.
        vault_path: Path to the vault root.
    """
    pp = _pending_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    tmp = pp.with_suffix(".jsonl.tmp")
    # Open the real queue (creating if needed) and lock it exclusively
    # before touching the tmp file. ``"a"`` keeps the existing content
    # intact and lets us create the file if it is absent.
    with open(pp, "a", encoding="utf-8") as real_fh:
        vault_common.flock_exclusive(real_fh)
        tmp_fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        # Replace while still holding the lock. After ``replace`` the
        # ``real_fh`` fd refers to the now-unlinked old inode; the lock
        # stays live on it (POSIX flock follows the fd, not the path),
        # blocking any concurrent appender that opened the same path
        # before we replaced it.
        tmp.replace(pp)


def _approve_entry_mutator(
    target: dict,
) -> Callable[[list[dict]], list[dict]]:
    """Build a _mutate_entries mutator marking *target* approved.

    A factory (rather than an inline lambda) so the loop variable is bound
    at call time — the TUI loop reassigns it after each keypress.
    """

    def mutator(cur: list[dict]) -> list[dict]:
        return [dict(e, status="approved") if e == target else e for e in cur]

    return mutator


def _remove_entry_mutator(
    target: dict,
) -> Callable[[list[dict]], list[dict]]:
    """Build a _mutate_entries mutator dropping *target* from the queue."""

    def mutator(cur: list[dict]) -> list[dict]:
        return [e for e in cur if e != target]

    return mutator


def _mutate_entries(
    mutator: Callable[[list[dict]], list[dict]], vault_path: Path | None = None
) -> list[dict]:
    """SEC-014: locked read-modify-write of the pending queue.

    The review TUI loads its entry list once and rewrote the whole queue
    from that stale snapshot, so a session hook appending while the TUI
    was open was silently dropped by the full-list replace. This applies
    *mutator* to the CURRENT queue content under LOCK_EX, using the same
    inode-recheck loop as ``append_to_pending`` (the queue is replaced by
    tmp+rename under the lock, so a blocked writer must re-open until its
    handle matches the path on disk). Returns the resulting queue.

    Args:
        mutator: Callable[[list[dict]], list[dict]] applied to the fresh
            queue content under the lock.
        vault_path: Path to the vault root.

    Returns:
        The queue as written (fresh parse), or a best-effort re-read if
        the inode never stabilised.
    """
    pp = _pending_path()
    pp.parent.mkdir(parents=True, exist_ok=True)
    tmp = pp.with_suffix(".jsonl.tmp")
    for _attempt in range(5):
        fd = os.open(str(pp), os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            vault_common.flock_exclusive(fh)
            try:
                try:
                    if os.fstat(fh.fileno()).st_ino != os.stat(pp).st_ino:
                        continue  # replaced while we waited — retry on new inode
                except OSError:
                    continue
                current: list[dict] = []
                fh.seek(0)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        current.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                updated = mutator(current)
                tmp_fd = os.open(str(tmp), os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as out:
                    for entry in updated:
                        out.write(json.dumps(entry) + "\n")
                tmp.replace(pp)
                return updated
            finally:
                vault_common.funlock(fh)
    return _read_entries()


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
    for older Claude Code and pi transcript path variants.

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

    candidates: list[Path] = []

    # Claude Code fallback: older entries omitted the "agent-" prefix.
    candidates.append(path.parent / f"agent-{path.stem}.jsonl")

    # pi fallback: support both historical location spellings.
    raw_str = str(path)
    if "/.pi/agent/sessions/" in raw_str:
        candidates.append(
            Path(raw_str.replace("/.pi/agent/sessions/", "/.pi/agent-sessions/"))
        )
    if "/.pi/agent-sessions/" in raw_str:
        candidates.append(
            Path(raw_str.replace("/.pi/agent-sessions/", "/.pi/agent/sessions/"))
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def _read_transcript_excerpt(
    entry: dict, n: int = _EXCERPT_LINES, vault_path: Path | None = None
) -> list[str]:
    """Read the first n text-bearing lines from the transcript.

    Args:
        entry: Pending summary entry dict containing ``transcript_path``.
        n: Number of lines to extract.
        vault_path: Path to the vault root.

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
            "  • The agent runtime cleaned up old transcripts",
            "  • The entry was queued before a path-handling fix (re-queue by running a new session)",
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
                # Supports Claude Code events (type=user/assistant) and
                # pi events (type=message with message.role=user/assistant).
                if isinstance(obj, dict):
                    role: str | None = None
                    raw_content: object = ""

                    msg = obj.get("message")
                    if isinstance(msg, dict):
                        role_raw = msg.get("role")
                        if isinstance(role_raw, str):
                            role = role_raw
                        raw_content = msg.get("content", "")

                    if role is None:
                        msg_type = obj.get("type")
                        if isinstance(msg_type, str) and msg_type in {
                            "user",
                            "assistant",
                        }:
                            role = msg_type
                            raw_content = obj.get("content", "")

                    if role not in {"user", "assistant"}:
                        continue

                    text = vault_common.extract_text_from_content(raw_content)
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


def _cmd_clear(vault_path: Path | None = None) -> None:
    """Remove all entries from the queue after confirmation.

    Args:
        vault_path: Path to the vault root.
    """
    entries = _read_entries()
    if not entries:
        print("Queue is already empty.")
        return
    answer = input(f"Remove all {len(entries)} pending entries? [y/N] ").strip().lower()
    if answer != "y":
        print("Cancelled.")
        return
    _write_entries([], vault_path=vault_path)
    print("Queue cleared.")


# ---------------------------------------------------------------------------
# TUI helpers
# ---------------------------------------------------------------------------

_POPUP_MIN_H: int = 3  # top/bottom border + one content line
_POPUP_MIN_W: int = 12  # side borders + padding + minimal readable text


def _clamp_selected(selected: int, count: int) -> int:
    """Clamp a selection index to the valid range for a list of ``count`` items.

    Args:
        selected: Current selection index (may be out of range after pops).
        count: Number of items in the list.

    Returns:
        An index in ``[0, count - 1]``, or 0 when the list is empty.
    """
    if count <= 0:
        return 0
    return max(0, min(selected, count - 1))


def _popup_dims(h: int, w: int, n_lines: int) -> tuple[int, int, int, int] | None:
    """Compute popup geometry for a terminal of ``h`` rows by ``w`` columns.

    Args:
        h: Terminal height in rows.
        w: Terminal width in columns.
        n_lines: Number of content lines the popup should display.

    Returns:
        ``(pop_h, pop_w, top, left)`` for ``curses.newwin``, or None when the
        terminal is too small to host a popup.
    """
    pop_h = min(h - 4, n_lines + 4)
    pop_w = min(w - 4, 100)
    if pop_h < _POPUP_MIN_H or pop_w < _POPUP_MIN_W:
        return None
    top = (h - pop_h) // 2
    left = (w - pop_w) // 2
    return pop_h, pop_w, top, left


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
    dims = _popup_dims(h, w, len(lines))
    if dims is None:
        # Terminal too small for a popup — show a one-line hint instead.
        try:
            stdscr.addstr(
                h - 1, 0, "Terminal too small for popup — press any key."[: w - 1]
            )
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.getch()
        return ord("q")
    pop_h, pop_w, top, left = dims

    win = curses.newwin(pop_h, pop_w, top, left)
    win.keypad(True)

    inner_h = pop_h - 2
    inner_w = pop_w - 4
    offset = 0
    closing_key = ord("q")
    while True:
        try:
            win.clear()
            win.box()
        except curses.error:
            pass
        if title:
            try:
                win.addstr(0, 2, f" {title[: max(0, pop_w - 6)]} ")
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


def _run_tui(stdscr, vault_path: Path | None = None) -> None:
    """Main curses event loop for the review TUI.

    ARC-013: the loop machinery (init, scroll sync, j/k navigation, header
    and status bars) lives in ``vault_tui.run_list_view``; this function
    supplies only the row renderer and the key handler.

    Args:
        stdscr: The curses window provided by ``curses.wrapper``.
        vault_path: Path to the vault root.
    """
    import curses

    from vault_tui import run_list_view

    entries = _read_entries()
    if not entries:
        stdscr.clear()
        stdscr.addstr(1, 2, "No pending sessions in queue.")
        stdscr.addstr(2, 2, "Press any key to exit.")
        stdscr.refresh()
        stdscr.getch()
        return

    status_cell = [""]

    def _render_entry(stdscr_, entry, y, is_selected, w) -> None:
        status = entry.get("status", "")
        prefix = {
            "approved": "[A] ",
            "rejected": "[R] ",
        }.get(status, "    ")
        line = (prefix + _entry_summary(entry))[: w - 1]
        line = line.ljust(w - 1)
        attr = curses.A_REVERSE if is_selected else curses.A_NORMAL
        if status == "approved":
            attr |= curses.A_BOLD
        stdscr_.addstr(y, 0, line, attr)

    def _on_key(key: int, selected: int) -> str | int | None:
        # Dump transcript excerpt
        if key in (ord("d"), ord("\n"), curses.KEY_ENTER, 10, 13):
            sel = selected
            while entries:
                entry = entries[sel]
                excerpt = _read_transcript_excerpt(entry, vault_path=vault_path)
                closing = _show_popup(stdscr, excerpt, title="Transcript Excerpt")

                if closing == ord("y"):
                    target = entries[sel]
                    # run_list_view's contract: mutate the rows list in
                    # place — it re-clamps against len(rows) each frame.
                    entries[:] = _mutate_entries(
                        _approve_entry_mutator(target), vault_path
                    )
                    status_cell[0] = f"Entry {sel + 1} approved."
                    if sel + 1 >= len(entries):
                        break  # approved the final entry — close the popup
                    sel += 1
                elif closing == ord("n"):
                    target = entries[sel]
                    entries[:] = _mutate_entries(
                        _remove_entry_mutator(target), vault_path
                    )
                    status_cell[0] = "Entry removed from queue."
                    sel = _clamp_selected(sel, len(entries))
                    # After y/n: show next entry's transcript automatically
                else:
                    break  # any other key just closes the popup

            if not entries:
                return "quit"  # queue drained inside the popup
            return _clamp_selected(sel, len(entries))

        # Approve
        if key == ord("y"):
            target = entries[selected]
            entries[:] = _mutate_entries(_approve_entry_mutator(target), vault_path)
            status_cell[0] = f"Entry {selected + 1} approved."
            return min(selected + 1, len(entries) - 1)

        # Reject (remove from queue)
        if key == ord("n"):
            target = entries[selected]
            entries[:] = _mutate_entries(_remove_entry_mutator(target), vault_path)
            if not entries:
                return "quit"
            status_cell[0] = "Entry removed from queue."
            return None

        # Skip
        if key == ord("s"):
            status_cell[0] = "Skipped."
            return min(selected + 1, len(entries) - 1)

        # Quit
        if key in (ord("q"), 27):  # q or ESC
            return "quit"
        return None

    run_list_view(
        stdscr,
        entries,
        _render_entry,
        _on_key,
        title=lambda _sel: f"Vault Review — {len(entries)} pending sessions",
        footer_keys="j/k:nav  Enter/d:dump(y/n inside)  y:approve  n:reject  s:skip  q:quit",
        status=status_cell,
    )


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

    # QA-001: Replace module-level VAULT_ROOT with try/finally restore pattern
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = vault_path
    # ARC-001: clear caches so lru_cache-memoized load_config() and
    # resolve_vault() observe the new VAULT_ROOT instead of stale values.
    vault_common.clear_config_cache()
    vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]

    try:
        if args.list:
            _cmd_list()
            return

        if args.clear:
            _cmd_clear(vault_path=vault_path)
            return

        # Auto-migrate on every startup (silent — fixes old entries in-place)
        vault_common.migrate_pending_paths(dry_run=False, vault=vault_path)

        # Check for pending sessions before attempting curses
        entries = _read_entries()
        if not entries:
            print("No pending sessions.")
            return

        # Try curses; fall back to --list mode if terminal doesn't support it
        try:
            import curses

            curses.wrapper(lambda stdscr: _run_tui(stdscr, vault_path=vault_path))
        except Exception:  # noqa: BLE001
            print(
                "Warning: terminal does not support curses, falling back to --list mode.",
                file=sys.stderr,
            )
            _cmd_list()

    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(0)
    finally:
        vault_common.VAULT_ROOT = original_vault_root
        # ARC-001: flush caches on restore so subsequent code sees the original vault.
        vault_common.clear_config_cache()
        vault_common.resolve_vault.cache_clear()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
