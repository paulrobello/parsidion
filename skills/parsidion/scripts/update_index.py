#!/usr/bin/env python3
"""Rebuild the resolved vault's CLAUDE.md index file.

Thin re-export shim over the ``cli.index`` package (ARC-005). The bulk of
the original 1,080-line God module — shared tuning constants, NamedTuple
record types, per-note parsing, two-pass index assembly, Markdown
rendering for TAGS.md and per-folder MANIFEST.md files, SQLite
``note_index`` upsert, and the post-index ``build_graph.py`` invocation —
has moved into focused submodules under ``cli.index``; the public +
private surface the original exposed remains importable from
``update_index`` so every ``import update_index`` consumer, the test
suite's attribute access (``update_index.build_index``,
``update_index._extract_summary``, ``update_index._singleton_guard``,
``update_index.pid_file``, ``update_index.SUMMARY_MAX_CHARS``, …), and
the ``uv run update_index.py`` entry point keep working byte-for-byte.

What stays here and why:
    ``pid_file``, ``_write_pid``, ``_release_pid``, ``_singleton_guard``,
    and the ``_is_process_running`` alias stay defined in this entry shim.
    ``tests/test_index_enhancements.py`` patches
    ``update_index._is_process_running`` and ``update_index.os.getpid``
    (module-attribute patches), and ``_singleton_guard`` calls
    ``_is_process_running`` as a bare name that must resolve in the
    module the test patches — so the singleton cluster stays put, along
    with the ``os`` re-export the ``getpid`` patch rides on. ``main``
    stays too: it weaves the singleton guard, the inline
    ``__file__``-relative ``build_embeddings.py`` discovery, and the
    par-mem/embeddings spawn into one entry point that is simpler to
    keep at the scripts root than to relocate with adjusted path math.

Walks the vault tree, parses frontmatter from all notes, and generates a
lean CLAUDE.md (stats, conventions, recent activity, folder pointers),
TAGS.md (full tag cloud for summarizer tag reuse), and per-folder
MANIFEST.md files (detailed note listings in table format).
Uses only Python stdlib.
"""

import argparse  # noqa: F401 — re-exported (update_index.argparse) for backwards compat
import atexit
import json  # noqa: F401 — re-exported (update_index.json) for backwards compat
import os
import re  # noqa: F401 — re-exported (update_index.re) for backwards compat
import subprocess  # noqa: F401 — re-exported (update_index.subprocess) for backwards compat
import sys
import time
from collections import Counter  # noqa: F401 — re-exported (update_index.Counter) for backwards compat
from datetime import datetime, timedelta  # noqa: F401 — re-exported for backwards compat
from pathlib import Path
from typing import NamedTuple  # noqa: F401 — re-exported (update_index.NamedTuple) for backwards compat

from vault_common import (
    all_vault_notes_walk,  # noqa: F401 — re-exported for backwards compat
    ensure_vault_dirs,  # noqa: F401 — re-exported for backwards compat
    extract_title,  # noqa: F401 — re-exported (update_index.extract_title)
    get_body,  # noqa: F401 — re-exported for backwards compat
    get_config,
    get_embeddings_db_path,
    git_commit_vault,
    is_process_running,
    parse_frontmatter,  # noqa: F401 — re-exported for backwards compat
    resolve_vault,
    write_hook_event,
)
from vault_index import drain_parse_warnings, record_parse_warning  # noqa: F401 — re-exports
from vault_fs import atomic_write_text  # noqa: F401 — re-exported (update_index.atomic_write_text)

import parmem_backend  # noqa: F401 — re-exported (update_index.parmem_backend) for backwards compat

# ---------------------------------------------------------------------------
# Re-exports from cli.index.* — every symbol the original update_index.py
# exposed remains importable from ``update_index``. Function objects are
# immutable so ``from cli.index.X import f`` + ``update_index.f(...)`` is a
# stable binding for external callers and the test suite.
# ---------------------------------------------------------------------------
from cli.index._common import (  # noqa: F401 — re-exports
    FOLDER_ORDER,
    RECENT_DAYS,
    RECENT_MAX,
    STALE_DAYS,
    SUMMARY_MAX_CHARS,
)
from cli.index.db import _write_note_index_to_db  # noqa: F401 — re-export
from cli.index.graph import (  # noqa: F401 — re-exports
    _find_build_graph_script,
    _rebuild_graph,
)
from cli.index.models import NoteEntry, NoteRecord  # noqa: F401 — re-exports
from cli.index.parse import (  # noqa: F401 — re-exports
    _WIKILINK_RE,
    _extract_summary,
    _extract_title,
    _extract_wikilink_stems,
    _folder_name,
    _wikilink,
)
from cli.index.build import (  # noqa: F401 — re-exports
    _build_note_db_rows,
    _compute_incoming_link_counts,
    build_index,
)
from cli.index.render import build_manifests, build_tags_md  # noqa: F401 — re-exports
from cli.index.cli import _parse_args  # noqa: F401 — re-export


# ---------------------------------------------------------------------------
# Singleton PID guard — kept in the entry shim because the test suite patches
# ``update_index._is_process_running`` and ``update_index.os.getpid``, and
# ``_singleton_guard`` calls ``_is_process_running`` as a bare name that must
# resolve in the patched module's globals. See the module docstring.
# ---------------------------------------------------------------------------


def pid_file(vault: Path | None = None) -> Path:
    """Return the PID file path resolved against *vault* (or the default).

    ARC-003: *vault* is threaded explicitly from ``main()``. When omitted,
    falls back to :func:`resolve_vault` so legacy callers (e.g. tests that
    relied on the previous ``vault_common.VAULT_ROOT`` mutation) still get a
    usable path.
    """
    if vault is None:
        # Last-resort fallback for any caller that didn't thread the path.
        return resolve_vault() / "index.pid"
    return vault / "index.pid"


# QA-007: _is_process_running removed — now imported from vault_common.
# Local alias preserves all existing call sites unchanged and is the
# attribute ``tests/test_index_enhancements.py`` patches.
_is_process_running = is_process_running


def _write_pid(vault: Path) -> None:
    """Atomically claim the PID file for this process.

    Uses ``O_CREAT | O_EXCL`` so the claim itself is the exclusivity check --
    two concurrent processes racing to create the file can never both
    succeed. Raises ``FileExistsError`` if the file already exists.
    """
    fd = os.open(pid_file(vault), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode("utf-8"))
    finally:
        os.close(fd)


def _release_pid(vault: Path) -> None:
    """Remove the PID file at process exit."""
    try:
        _pf = pid_file(vault)
        if _pf.exists() and _pf.read_text(encoding="utf-8").strip() == str(os.getpid()):
            _pf.unlink()
    except Exception:  # noqa: BLE001
        pass  # best-effort cleanup


def _singleton_guard(vault: Path) -> None:
    """Exit early if another update_index is already running.

    The claim itself (``_write_pid``) is atomic, which closes the
    check-then-write race of the previous implementation: reading the PID
    file, deciding no one owns it, and only then writing our own PID left a
    window where two concurrent invocations could both pass the check. If
    the atomic claim finds the file already present, the owning PID is
    inspected; a dead owner means a stale lock, which is removed and the
    atomic claim retried exactly once before giving up.
    """
    try:
        _write_pid(vault)
    except FileExistsError:
        try:
            existing_pid: int | None = int(
                pid_file(vault).read_text(encoding="utf-8").strip()
            )
        except (OSError, ValueError):
            existing_pid = None

        if (
            existing_pid is not None
            and existing_pid != os.getpid()
            and _is_process_running(existing_pid)
        ):
            print(
                f"update_index is already running (PID {existing_pid}). Exiting.",
                file=sys.stderr,
            )
            sys.exit(0)

        # Stale lock (owner process dead, or file unreadable/corrupt) --
        # remove it and retry the atomic claim exactly once.
        try:
            pid_file(vault).unlink()
        except OSError:
            pass
        try:
            _write_pid(vault)
        except FileExistsError:
            # Lost a race against another process re-claiming the file in
            # the gap between our unlink and retry -- bail out defensively.
            print(
                "update_index: lost the singleton race to another process. Exiting.",
                file=sys.stderr,
            )
            sys.exit(0)

    # ARC-003: register the release handler with this run's vault bound in,
    # so the PID file we just created is the one removed at exit.
    atexit.register(_release_pid, vault)


def _parse_note_record(note_path: Path, vault: Path) -> NoteRecord | None:
    """Read and parse a single note, returning its first-pass fields.

    Stays in the entry shim (not in ``cli.index.parse``) because the test
    suite patches ``update_index.parse_frontmatter`` and the bare-name call
    below resolves through this module's globals — so the patch takes effect
    here, and ``build_index`` reaches the patched function via a late
    ``import update_index`` (see ``cli/index/build.py``).

    Returns ``None`` when the file cannot be read (matching the original
    first-pass skip-on-``OSError``/``UnicodeDecodeError`` behaviour).

    Non-string tags from a legacy parser are coerced to ``str(tag)`` with a
    stderr warning (mirroring ``build_embeddings.py``) so the note_index and
    note_embeddings tables never desync; the caller counts the returned tags.
    """
    try:
        content: str = note_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    fm: dict[str, object] = parse_frontmatter(content)
    title: str = _extract_title(content, note_path.stem)
    summary: str = _extract_summary(content)
    folder: str = _folder_name(note_path, vault)

    # Collect tags
    tags_raw: object = fm.get("tags")
    tags_list: list[str] = []
    if isinstance(tags_raw, list):
        for tag in tags_raw:
            if not isinstance(tag, str) and tag is not None:
                # Defensive: numeric-looking tags (e.g. `tags: [2026]`)
                # coerced by older parsers must not be silently dropped --
                # build_embeddings.py writes str(t), so dropping here would
                # desync the note_index and note_embeddings tables.
                _tag_warning = (
                    f"update_index: coercing non-string tag {tag!r} "
                    f"to string in {note_path}"
                )
                print(_tag_warning, file=sys.stderr)
                record_parse_warning(_tag_warning)
                tag = str(tag)
            if isinstance(tag, str) and tag:
                tags_list.append(tag)

    # Mtime
    try:
        mtime: float = note_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    # Collect wikilink stems from related field
    related_stems: list[str] = _extract_wikilink_stems(fm.get("related"))

    return NoteRecord(
        content=content,
        frontmatter=fm,
        title=title,
        summary=summary,
        folder=folder,
        tags=tags_list,
        mtime=mtime,
        related_stems=related_stems,
    )


def main() -> None:
    """Entry point: rebuild the index, write CLAUDE.md, and generate MANIFEST.md files."""
    _hook_start = time.monotonic()
    args = _parse_args()

    # ARC-003: resolve once, thread explicitly. No more mutation of
    # ``vault_common.VAULT_ROOT`` and no more ``resolve_vault.cache_clear()``
    # dance — ``args.vault`` is passed as the ``explicit`` argument to
    # ``resolve_vault()`` here and the resolved path flows through the rest
    # of ``main()`` as the ``vault`` parameter.
    vault_path = resolve_vault(explicit=args.vault, cwd=os.getcwd())

    _singleton_guard(vault_path)
    (
        content,
        note_count,
        tag_count,
        folder_notes,
        db_rows,
        tag_counter,
    ) = build_index(vault=vault_path)
    now_str: str = datetime.now().strftime("%Y-%m-%d %H:%M")

    index_path: Path = vault_path / "CLAUDE.md"
    # QA-017: atomic write — CLAUDE.md is read by session_start_hook at every
    # session start, so a half-written file injects truncated context into a
    # live agent session. Same pattern vault_links/vault_doctor already use.
    atomic_write_text(index_path, content)

    tags_path: Path = vault_path / "TAGS.md"
    atomic_write_text(tags_path, build_tags_md(tag_counter, now_str))

    manifest_paths: list[Path] = build_manifests(folder_notes, vault=vault_path)
    manifest_count: int = len(manifest_paths)

    # Commit CLAUDE.md + TAGS.md + all MANIFEST.md files together
    commit_paths: list[Path] = [index_path, tags_path] + manifest_paths
    git_commit_vault(
        "chore(vault): rebuild index, tags, and manifests",
        paths=commit_paths,
        vault=vault_path,
    )

    current_stems: set[str] = {row.stem for row in db_rows}
    _write_note_index_to_db(db_rows, current_stems, vault=vault_path)

    print(
        f"Updated CLAUDE.md: {note_count} notes indexed, {tag_count} tags; "
        f"TAGS.md written; {manifest_count} MANIFEST.md file(s) generated"
    )

    # Surface frontmatter parse warnings (stderr is swallowed by hook callers)
    # via the same hook_events.log read by `vault-stats --hooks N`.
    parse_warnings = drain_parse_warnings()
    event_extra: dict[str, object] = {
        "notes_indexed": note_count,
        "tags": tag_count,
    }
    if parse_warnings:
        event_extra["parse_warnings"] = len(parse_warnings)
        event_extra["parse_warning_samples"] = parse_warnings[:5]
    write_hook_event(
        hook="IndexRebuild",
        project=vault_path.name,
        duration_ms=(time.monotonic() - _hook_start) * 1000,
        vault=vault_path,
        **event_extra,
    )

    # Update embeddings.db in the background when enabled.
    # Incremental when the DB already exists; full rebuild when it does not.
    # ARC-011: stderr is redirected to a log file so silent failures are
    # visible.  Check ~/.claude/logs/parsidion-embed.log when embeddings
    # seem stale.
    #
    # QA-009: a user without the `search` extra sees a cheerful "launched"
    # banner while the child exits 1 with an ImportError in the log file and
    # embeddings silently never build. After spawning, poll() briefly — if the
    # child died within the grace window it almost certainly failed to import
    # (a real embed run takes seconds), so report that explicitly and surface
    # the log path instead of the false success banner.
    # QA-021: close the parent's copy of `_embed_log` after spawn. The fd is
    # intentionally inherited by the detached child, but the parent leaking
    # its own handle is unnecessary.
    if get_config("embeddings", "enabled", True):
        db_path = get_embeddings_db_path(vault=vault_path)
        build_script = Path(__file__).parent / "build_embeddings.py"
        if build_script.exists():
            cmd = ["uv", "run", "--no-project", str(build_script)]
            if db_path.exists():
                cmd.append("--incremental")
                label = "incremental"
            else:
                label = "full"
            _embed_log_dir = Path.home() / ".claude" / "logs"
            _embed_log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            _embed_log = open(  # noqa: SIM115
                _embed_log_dir / "parsidion-embed.log",
                "a",
                encoding="utf-8",
            )
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=_embed_log,
                start_new_session=True,
            )
            # Give the child a short grace window. A successful embedding run
            # takes seconds (loads a ~67 MB ONNX model); if it has already
            # exited within 1 s, it almost certainly failed to import.
            import time as _time

            _time.sleep(1.0)
            if proc.poll() is None:
                print(f"Embeddings: {label} rebuild launched in background")
            else:
                print(
                    f"Embeddings: {label} rebuild FAILED to start "
                    f"(child exited {proc.returncode}). "
                    f"See {_embed_log_dir / 'parsidion-embed.log'}. "
                    f"Hint: uv tool install --editable '.[search]'",
                    file=sys.stderr,
                )
            try:
                _embed_log.close()
            except OSError:
                pass

    # par-mem freshness trigger: when the optional par-mem backend resolves,
    # kick a detached incremental `par-mem index` so the code-memory graph
    # tracks the vault without blocking this run (see docs/PAR-MEM.md).
    # Independent of embeddings.enabled — par-mem is its own index.
    if parmem_backend.resolve_parmem_backend(vault_path):
        if parmem_backend.spawn_background_index(vault_path):
            print("par-mem: background index launched")

    if args.rebuild_graph:
        # ENH-002: graph_incremental is opt-in via --graph-incremental OR
        # summarizer.graph_incremental in config.yaml. CLI flag wins; config
        # is the silent default so nightly summarizer runs can opt in once.
        incremental = args.graph_incremental or bool(
            get_config("summarizer", "graph_incremental", False)
        )
        _rebuild_graph(include_daily=args.graph_include_daily, incremental=incremental)


if __name__ == "__main__":  # pragma: no cover — entry shim
    main()
