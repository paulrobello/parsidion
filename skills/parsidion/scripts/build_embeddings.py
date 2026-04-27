#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "fastembed>=0.6.0,<1.0",
#   "sqlite-vec>=0.1.6,<1.0",
# ]
# ///
"""Build (or update) the semantic search index for Parsidion vault.

Creates <resolved vault>/embeddings.db with cosine-similarity vectors for every
vault note. Uses BAAI/bge-small-en-v1.5 (384-dim, ~67 MB ONNX, CPU-only).

Usage:
    uv run build_embeddings.py              # full rebuild
    uv run build_embeddings.py --incremental  # update only changed / new notes
    uv run build_embeddings.py --dry-run    # preview without writing
    uv run build_embeddings.py --model <id> # use a different fastembed model
"""

import argparse
import sqlite3
import struct
import time
from pathlib import Path

import sqlite_vec  # type: ignore[import-untyped]
from fastembed import TextEmbedding  # type: ignore[import-untyped]

import vault_common

_DEFAULT_MODEL: str = "BAAI/bge-small-en-v1.5"
_EMBED_DIM: int = 384
_MAX_TEXT_CHARS: int = 1500  # ~400 tokens for bge-small


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the embeddings database and load the sqlite-vec extension.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        An open sqlite3.Connection with WAL mode and the note_embeddings table
        already created.
    """
    conn = sqlite3.connect(db_path)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS note_embeddings (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            stem      TEXT    NOT NULL UNIQUE,
            path      TEXT    NOT NULL,
            folder    TEXT    NOT NULL DEFAULT '',
            title     TEXT    NOT NULL DEFAULT '',
            tags      TEXT    NOT NULL DEFAULT '',
            mtime     REAL    NOT NULL DEFAULT 0.0,
            embedding BLOB    NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_stem ON note_embeddings(stem)")
    conn.commit()
    vault_common.ensure_note_index_schema(conn)
    return conn


def build_embed_text(title: str, tags_str: str, body: str) -> str:
    """Concatenate title, tags, and body for embedding, truncated to _MAX_TEXT_CHARS.

    Args:
        title: Note title (from first # heading or filename stem).
        tags_str: Comma-separated tag string.
        body: Note body (after frontmatter).

    Returns:
        Text ready for embedding, truncated to _MAX_TEXT_CHARS characters.
    """
    text = f"{title}\n{tags_str}\n{body}"
    return text[:_MAX_TEXT_CHARS]


def get_stored_mtimes(conn: sqlite3.Connection) -> dict[str, float]:
    """Return a dict mapping note stem → stored mtime from the database.

    Args:
        conn: Open database connection.

    Returns:
        Dict of {stem: mtime} for all rows in note_embeddings.
    """
    cursor = conn.execute("SELECT stem, mtime FROM note_embeddings")
    return {row[0]: row[1] for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _pack_vector(vec: list[float]) -> bytes:
    """Pack a float32 vector as a BLOB for sqlite-vec storage.

    Args:
        vec: List of float values.

    Returns:
        Packed binary representation.
    """
    return struct.pack(f"{len(vec)}f", *vec)


def _note_title(note_path: Path, content: str) -> str:
    """Extract note title from first # heading, falling back to filename stem.

    Delegates to ``vault_common.extract_title`` — the canonical implementation.
    See ARC-009.

    Args:
        note_path: Path to the note file.
        content: Full note content.

    Returns:
        Title string.
    """
    return vault_common.extract_title(content, note_path.stem)


def embed_and_write(
    notes_to_embed: list[tuple[Path, str, str]],
    model_name: str,
    conn: sqlite3.Connection,
    dry_run: bool,
) -> int:
    """Embed a batch of notes and write them to the database.

    Args:
        notes_to_embed: List of (note_path, stem, embed_text) tuples.
        model_name: fastembed model ID to use.
        conn: Open database connection.
        dry_run: If True, print actions without writing.

    Returns:
        Number of records written (0 in dry_run mode).
    """
    if not notes_to_embed:
        return 0

    if dry_run:
        print(f"[dry-run] Would embed {len(notes_to_embed)} notes with {model_name}")
        return 0

    texts = [t for _, _, t in notes_to_embed]
    model = TextEmbedding(model_name=model_name)
    vectors = list(model.embed(texts))

    with conn:
        for (note_path, stem, _), vec in zip(notes_to_embed, vectors, strict=False):
            try:
                content = note_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            fm = vault_common.parse_frontmatter(content)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            # ARC-004: canonical tag format is ", ".join(sorted(tags)) — sorted
            # alphabetically with a single space after each comma.  Matches the
            # format written by update_index.py for consistent LIKE matching.
            tags_str = ", ".join(sorted(str(t) for t in tags)) if tags else ""
            title = _note_title(note_path, content)
            folder = (
                note_path.parent.name
                if note_path.parent != vault_common.VAULT_ROOT
                else ""
            )
            try:
                mtime = note_path.stat().st_mtime
            except OSError:
                mtime = 0.0

            blob = _pack_vector(list(vec))

            conn.execute(
                """
                INSERT INTO note_embeddings (stem, path, folder, title, tags, mtime, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stem) DO UPDATE SET
                    path=excluded.path,
                    folder=excluded.folder,
                    title=excluded.title,
                    tags=excluded.tags,
                    mtime=excluded.mtime,
                    embedding=excluded.embedding
                """,
                (stem, str(note_path), folder, title, tags_str, mtime, blob),
            )

    return len(notes_to_embed)


# ---------------------------------------------------------------------------
# Build modes
# ---------------------------------------------------------------------------


def _collect_notes() -> list[tuple[Path, str, str]]:
    """Collect all vault notes and build their embed texts.

    Returns:
        List of (note_path, stem, embed_text) tuples.
    """
    results: list[tuple[Path, str, str]] = []
    for note_path in vault_common.all_vault_notes():
        try:
            content = note_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        fm = vault_common.parse_frontmatter(content)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        # ARC-004: canonical tag format is ", ".join(sorted(tags))
        tags_str = ", ".join(sorted(str(t) for t in tags)) if tags else ""
        title = _note_title(note_path, content)
        body = vault_common.get_body(content)
        embed_text = build_embed_text(title, tags_str, body)
        results.append((note_path, note_path.stem, embed_text))

    return results


def full_rebuild(vault_root: Path, model_name: str, dry_run: bool) -> None:
    """Delete all existing embeddings and re-embed every note from scratch.

    Args:
        vault_root: Path to the vault root directory.
        model_name: fastembed model ID to use.
        dry_run: If True, print actions without writing.
    """
    db_path = vault_common.get_embeddings_db_path(vault=vault_root)
    notes = _collect_notes()

    if dry_run:
        print(f"[dry-run] Would DELETE all rows and re-embed {len(notes)} notes")
        print(f"[dry-run] DB path: {db_path}")
        return

    conn = open_db(db_path)
    with conn:
        conn.execute("DELETE FROM note_embeddings")

    written = embed_and_write(notes, model_name, conn, dry_run=False)
    conn.close()
    print(f"Full rebuild: embedded {written} notes")


def incremental_update(vault_root: Path, model_name: str, dry_run: bool) -> None:
    """Embed only new or changed notes; delete embeddings for removed notes.

    Args:
        vault_root: Path to the vault root directory.
        model_name: fastembed model ID to use.
        dry_run: If True, print actions without writing.
    """
    db_path = vault_common.get_embeddings_db_path(vault=vault_root)

    if not db_path.exists():
        print("No embeddings.db found — running full rebuild instead.")
        full_rebuild(vault_root, model_name, dry_run)
        return

    conn = open_db(db_path)
    stored = get_stored_mtimes(conn)

    current_notes = vault_common.all_vault_notes()
    current_stems = {n.stem for n in current_notes}

    # Deleted notes
    deleted_stems = [s for s in stored if s not in current_stems]
    if deleted_stems and not dry_run:
        with conn:
            conn.executemany(
                "DELETE FROM note_embeddings WHERE stem = ?",
                [(s,) for s in deleted_stems],
            )
    elif deleted_stems and dry_run:
        print(f"[dry-run] Would delete {len(deleted_stems)} removed notes")

    # New or changed notes
    to_embed: list[tuple[Path, str, str]] = []
    for note_path in current_notes:
        try:
            mtime = note_path.stat().st_mtime
        except OSError:
            continue

        stem = note_path.stem
        if stem not in stored or stored[stem] != mtime:
            try:
                content = note_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            fm = vault_common.parse_frontmatter(content)
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [tags]
            # ARC-004: canonical tag format is ", ".join(sorted(tags))
            tags_str = ", ".join(sorted(str(t) for t in tags)) if tags else ""
            title = _note_title(note_path, content)
            body = vault_common.get_body(content)
            embed_text = build_embed_text(title, tags_str, body)
            to_embed.append((note_path, stem, embed_text))

    new_count = sum(1 for p, s, _ in to_embed if s not in stored)
    changed_count = sum(1 for p, s, _ in to_embed if s in stored)

    print(f"{changed_count} changed, {new_count} new, {len(deleted_stems)} deleted")

    written = embed_and_write(to_embed, model_name, conn, dry_run)
    conn.close()

    if not dry_run and written:
        print(f"Updated {written} notes")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Parse CLI args and run full rebuild or incremental update."""
    parser = argparse.ArgumentParser(
        description="Build or update the Parsidion vault semantic search index.",
    )
    parser.add_argument(
        "--vault",
        "-V",
        type=str,
        help="Vault name or path (default: current project-local or default vault)",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        default=False,
        help="Update only new/changed notes instead of a full rebuild.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview actions without writing to disk.",
    )
    config_model: str = vault_common.get_config("embeddings", "model", _DEFAULT_MODEL)
    parser.add_argument(
        "--model",
        default=config_model,
        metavar="MODEL",
        help=f"fastembed model ID to use (default from config: {config_model}).",
    )
    args = parser.parse_args()

    if "bge-small" in args.model.lower():
        print(
            f"Note: {args.model} downloads ~67 MB on first use "
            "(cached in ~/.cache/fastembed after that)."
        )

    # Resolve vault path first (needed for model dimension check)
    vault_path = vault_common.resolve_vault(explicit=args.vault)

    # If the model changed since the last build, incremental is unsafe —
    # existing vectors have a different dimension. Force full rebuild.
    db_path = vault_common.get_embeddings_db_path(vault=vault_path)
    if args.incremental and db_path.exists():
        conn_check = open_db(db_path)
        row = conn_check.execute(
            "SELECT embedding FROM note_embeddings LIMIT 1"
        ).fetchone()
        conn_check.close()
        if row is not None:
            import struct as _struct

            stored_dim = len(_struct.unpack(f"{len(row[0]) // 4}f", row[0]))
            # Load model briefly to check its output dimension
            from fastembed import TextEmbedding as _TE  # type: ignore[import-untyped]

            probe = list(_TE(model_name=args.model).embed(["probe"]))[0]
            if len(probe) != stored_dim:
                print(
                    f"Model dimension mismatch (stored={stored_dim}, "
                    f"new={len(probe)}) — forcing full rebuild."
                )
                args.incremental = False

    # QA-001: Replace VAULT_ROOT with try/finally restore pattern
    original_vault_root = vault_common.VAULT_ROOT
    vault_common.VAULT_ROOT = vault_path

    try:
        start = time.time()
        if args.incremental:
            incremental_update(vault_path, args.model, args.dry_run)
        else:
            full_rebuild(vault_path, args.model, args.dry_run)

        elapsed = time.time() - start
        if not args.dry_run:
            print(f"Done in {elapsed:.1f}s using {args.model}")
    finally:
        vault_common.VAULT_ROOT = original_vault_root


if __name__ == "__main__":
    main()
