#!/usr/bin/env python3
"""Generate a synthetic Parsidion vault for the SessionStart latency bench.

ENH-023: creates a vault with N realistic notes (frontmatter, tags,
preferential-attachment ``related`` links, spread mtimes) and populates its
``note_index`` via the real ``update_index.py`` so the benched hook exercises
the production retrieval path (DB-first seed queries, full-table graph
metadata load, compact-index assembly).

Deterministic for a given ``--seed``. Stdlib-only. The bench vault's
``config.yaml`` pins the nondeterministic legs off (semantic search, parsight,
AI selection, embeddings, git) so ``tools/bench/bench_session_start.py``
measures this repo's code, not the local daemon/model state.

The embeddings gate inside update_index reads config from the *resolved*
vault, so this script runs it with ``cwd`` and ``CLAUDE_VAULT`` both pointed
at the output vault — without that, a machine whose default vault enables
embeddings would trigger a background ``build_embeddings.py`` run against the
bench vault. It also registers the vault in a bench-local ``vaults.yaml``
(the SEC-P001 allowlist rejects unnamed throwaway paths) exposed to
subprocesses via ``XDG_CONFIG_HOME``; the bench driver reuses the same
registry for the hook subprocesses.
"""

from __future__ import annotations

import argparse
import os
import random
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "skills" / "parsidion" / "scripts"

# type -> vault folder (mirrors the real vault layout; see CLAUDE.md conventions)
_TYPE_FOLDERS = {
    "pattern": "Patterns",
    "debugging": "Debugging",
    "research": "Research",
    "tool": "Tools",
    "knowledge": "Knowledge",
    "language": "Languages",
    "framework": "Frameworks",
    "project": "Projects",
}
_ADJECTIVES = [
    "atomic",
    "cached",
    "cold",
    "deep",
    "detached",
    "incremental",
    "lazy",
    "legacy",
    "nested",
    "portable",
    "quiet",
    "recursive",
    "rooted",
    "silent",
    "skewed",
    "split",
    "stable",
    "stale",
]
_NOUNS = [
    "agent",
    "cache",
    "cli",
    "config",
    "daemon",
    "fixture",
    "graph",
    "hook",
    "index",
    "lock",
    "parser",
    "queue",
    "schema",
    "search",
    "session",
    "vault",
]
_TAGS = [
    "python",
    "vault",
    "hook",
    "index",
    "sqlite",
    "config",
    "ci",
    "search",
    "agent",
    "cli",
    "yaml",
    "git",
]
_SENTENCES = [
    "The {adj} {noun} path must degrade gracefully when the backing store is absent.",
    "Measure before optimizing: the {adj} {noun} looked slow but accounted for {n} ms total.",
    "A {noun} that fails silently is worse than one that fails loudly — add a log line.",
    "Keep the {noun} stdlib-only; the optional extras load lazily where they are needed.",
    "The {adj} {noun} reuses the previous result instead of rebuilding from scratch.",
    "Write the {noun} contract down as a test; the next refactor will thank you.",
    "Retry with backoff around the {adj} {noun} instead of assuming one shot succeeds.",
    "The {noun} is bounded by a budget constant pinned at the top of the driver.",
    "Prefer an explicit {noun} over an implicit one discovered by convention.",
    "Regenerate the {noun} fixtures when the schema changes; the CI drift gate catches it.",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a synthetic vault for the SessionStart bench (ENH-023)."
    )
    parser.add_argument(
        "--notes", type=int, default=500, help="note count (default 500)"
    )
    parser.add_argument(
        "--out", type=Path, required=True, help="output vault directory"
    )
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (default 42)")
    parser.add_argument(
        "--project",
        type=str,
        default="bench",
        help="project name baked into ~5%% of notes and matched by the bench cwd (default: bench)",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="write notes only; skip the update_index run and verification",
    )
    return parser.parse_args()


def _gen_bench_vault(notes: int, out: Path, seed: int, project: str) -> list[str]:
    """Write the synthetic vault; return the generated note stems."""
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    for folder in set(_TYPE_FOLDERS.values()):
        (out / folder).mkdir(parents=True, exist_ok=True)

    types = list(_TYPE_FOLDERS)
    today = date.today()
    stems: list[str] = []
    stem_set: set[str] = set()
    # Preferential attachment: stems weighted by 1 + in-degree, so a few notes
    # become hubs — the power-law-ish related distribution the audit measured.
    in_degree: dict[str, int] = {}
    today_ts = time.time()
    note_dates: dict[str, date] = {}

    for i in range(notes):
        w1 = rng.choice(_ADJECTIVES)
        w2 = rng.choice(_NOUNS)
        w3 = rng.choice(_NOUNS)
        stem = f"{w1}-{w2}-{w3}" if w2 != w3 else f"{w1}-{w2}"
        if stem in stem_set:
            stem = f"{stem}-{i}"
        stem_set.add(stem)
        stems.append(stem)

        note_type = types[i % len(types)]
        note_date = today - timedelta(days=rng.randint(0, 90))
        note_dates[stem] = note_date
        tag_count = rng.randint(2, 4)
        tags = rng.sample(_TAGS, tag_count)
        # ~5% carry the bench project (matched by find_notes_by_project),
        # ~10% a second project, the rest none.
        roll = rng.random()
        if roll < 0.05:
            note_project = project
        elif roll < 0.15:
            note_project = f"{project}-other"
        else:
            note_project = None

        # Out-degree: heavy tail capped at 6, ~15% orphans-by-choice.
        if rng.random() < 0.15:
            link_count = 0
        else:
            link_count = min(int(rng.paretovariate(2.2)), 6)
        links: list[str] = []
        if stems[:-1] and link_count:
            pool = stems[:-1]
            weights = [1 + in_degree.get(s, 0) for s in pool]
            # rng.choices supports weights; pick without replacement by retry
            chosen: set[str] = set()
            attempts = 0
            while len(chosen) < min(link_count, len(pool)) and attempts < 30:
                pick = rng.choices(pool, weights=weights, k=1)[0]
                chosen.add(pick)
                attempts += 1
            links = sorted(chosen)
            for link in links:
                in_degree[link] = in_degree.get(link, 0) + 1

        related = ", ".join(f'"[[{s}]]"' for s in links)
        project_line = f"project: {note_project}\n" if note_project else ""
        fm = (
            "---\n"
            f"date: {note_date.isoformat()}\n"
            f"type: {note_type}\n"
            f"tags: [{', '.join(tags)}]\n"
            f"{project_line}"
            f"confidence: {rng.choice(['high', 'medium', 'medium', 'low'])}\n"
            f"related: [{related}]\n"
            "provenance: observed\n"
            "---\n\n"
        )
        title = stem.replace("-", " ").title()
        body_lines = [f"# {title}", ""]
        for _ in range(rng.randint(4, 10)):
            body_lines.append(
                rng.choice(_SENTENCES).format(
                    adj=rng.choice(_ADJECTIVES),
                    noun=rng.choice(_NOUNS),
                    n=rng.randint(5, 900),
                )
            )
        body_lines.append("")
        note_path = out / _TYPE_FOLDERS[note_type] / f"{stem}.md"
        note_path.write_text(fm + "\n".join(body_lines), encoding="utf-8")

        # mtime: ~2% touched within the last 2 days (delta/recent visibility),
        # the rest spread across the note's age so find_recent_notes has work.
        if rng.random() < 0.02:
            mtime = today_ts - rng.randint(0, 2 * 86400)
        else:
            age_days = max((today - note_date).days, 1)
            mtime = today_ts - int(age_days * 86400 * rng.uniform(0.5, 1.0))
        os.utime(note_path, (mtime, mtime))

    _write_bench_config(out, project)
    return stems


def _write_bench_config(out: Path, project: str) -> None:
    """Pin every nondeterministic leg off (see module docstring)."""
    config = (
        "# Generated by tools/bench/gen_bench_vault.py (ENH-023) — determinism knobs.\n"
        "session_start_hook:\n"
        "  use_embeddings: false   # skip the semantic subprocess leg\n"
        "parsight:\n"
        "  enabled: false          # never spawn/probe the code-memory daemon\n"
        "embeddings:\n"
        "  enabled: true           # gates the note_index WRITE (see\n"
        "                         # _build_note_index); the semantic-search leg\n"
        "                         # stays off via use_embeddings above and the\n"
        "                         # embedder is never spawned in-process\n"
        "event_log:\n"
        "  enabled: true           # the driver reads stage timings from here\n"
        "git:\n"
        "  auto_commit: false      # bench vault has no .git anyway\n"
        f"# bench project name: {project}\n"
    )
    (out / "config.yaml").write_text(config, encoding="utf-8")


def _ensure_vault_registry(out: Path) -> Path:
    """Register the bench vault in a bench-local vaults.yaml (SEC-P001).

    resolve_vault() only accepts named vaults or the default vault, so a
    throwaway path under /tmp is rejected outright. The sanctioned pattern
    (same as tests/conftest.py's tmp_vault fixture) is a local registry
    pointed at by XDG_CONFIG_HOME. Lives in the vault's parent so several
    bench vaults under one root share it.
    """
    config_dir = out.parent / ".config" / "parsidion"
    config_dir.mkdir(parents=True, exist_ok=True)
    registry = config_dir / "vaults.yaml"
    entry_name = f"bench-{out.name}"
    existing = registry.read_text(encoding="utf-8") if registry.exists() else ""
    if str(out) in existing:
        return config_dir.parent
    lines = existing.splitlines() if existing else []
    if not any(line.strip() == "vaults:" for line in lines):
        lines.append("vaults:")
    lines.append(f"  {entry_name}: {out}")
    registry.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_dir.parent


def _build_note_index(out: Path) -> None:
    """Populate the bench vault's note_index via the real indexer functions.

    Why in-process instead of running ``update_index.py`` as a CLI: main()
    gates BOTH the note_index write and a background ``build_embeddings.py``
    spawn on the same ``embeddings.enabled`` key, and the spawned embedder
    resolves the *default* vault — so the CLI either skips the index write
    (embeddings off) or kicks off an embedding run against the user's real
    vault (embeddings on). Importing ``build_index`` +
    ``_write_note_index_to_db`` produces the identical note_index the CLI
    writes, with no spawn.

    Env is pinned (CLAUDE_VAULT + the registry's XDG_CONFIG_HOME) BEFORE the
    import so the config/cache resolution inside the indexer sees the bench
    vault regardless of the ambient environment this script was launched in.
    """
    xdg_config = _ensure_vault_registry(out)
    os.environ["CLAUDE_VAULT"] = str(out)
    os.environ["XDG_CONFIG_HOME"] = str(xdg_config)

    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        from update_index import _write_note_index_to_db, build_index
        from vault_path import get_embeddings_db_path
    finally:
        sys.path.remove(str(SCRIPTS_DIR))

    db_path = get_embeddings_db_path(vault=out)
    if not db_path.exists():
        # _write_note_index_to_db is a no-op when the db file is absent;
        # create the empty file so the schema+write path proceeds.
        sqlite3.connect(str(db_path)).close()

    _content, note_count, _tags, _folders, db_rows, _counter = build_index(vault=out)
    current_stems = {row.stem for row in db_rows}
    _write_note_index_to_db(db_rows, current_stems, vault=out)
    if note_count < 1:
        raise SystemExit("build_index indexed zero notes in the bench vault")


def _verify_index(out: Path, stems: list[str]) -> None:
    db = out / "embeddings.db"
    if not db.exists():
        raise SystemExit("update_index produced no embeddings.db — note_index missing")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = {r[0] for r in conn.execute("SELECT stem FROM note_index")}
    finally:
        conn.close()
    missing = sorted(set(stems) - rows)
    if missing:
        raise SystemExit(
            f"{len(missing)} generated notes missing from note_index "
            f"(first: {missing[:3]})"
        )


def main() -> None:
    args = _parse_args()
    start = time.perf_counter()
    stems = _gen_bench_vault(args.notes, args.out, args.seed, args.project)
    if not args.skip_index:
        _build_note_index(args.out)
        _verify_index(args.out, stems)
    elapsed = time.perf_counter() - start
    print(
        f"Generated {args.notes}-note bench vault at {args.out} "
        f"({'indexed' if not args.skip_index else 'index skipped'}, "
        f"{elapsed:.1f}s, project={args.project!r})"
    )


if __name__ == "__main__":
    main()
