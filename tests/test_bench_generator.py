"""Tests for the ENH-023 bench vault generator (tools/bench/gen_bench_vault.py).

Covers the plan's generator-validity criterion: generated notes pass the real
indexer cleanly, the note_index is fully populated, and generation is
deterministic for a fixed seed. The generator itself runs as a subprocess
(the same surface ``make bench-hooks`` drives), stdlib-only.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from vault_path import EXCLUDE_DIRS

REPO_ROOT = Path(__file__).resolve().parents[1]
GEN_SCRIPT = REPO_ROOT / "tools" / "bench" / "gen_bench_vault.py"
UPDATE_INDEX = REPO_ROOT / "skills" / "parsidion" / "scripts" / "update_index.py"


def _generate(out: Path, notes: int, seed: int = 42) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(GEN_SCRIPT),
            "--notes",
            str(notes),
            "--out",
            str(out),
            "--seed",
            str(seed),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"generator failed:\n{proc.stderr}"


class TestBenchGeneratorValidity:
    def test_generated_notes_populate_note_index(self, tmp_path: Path) -> None:
        """Generated notes pass the real indexer; every stem lands in note_index."""
        out = tmp_path / "vault"
        _generate(out, notes=25)

        db = out / "embeddings.db"
        assert db.exists(), "generator left no embeddings.db"
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = {
                stem: (incoming, related)
                for stem, incoming, related in conn.execute(
                    "SELECT stem, incoming_links, related FROM note_index"
                )
            }
        finally:
            conn.close()

        # build_index creates the full vault layout, including the Templates
        # symlink into the skill's template dir; pathlib glob follows
        # symlinks, so filter by the same EXCLUDE_DIRS the indexer uses.
        stems = {
            p.stem for p in out.glob("*/*.md") if p.parent.name not in EXCLUDE_DIRS
        }
        assert len(stems) == 25
        assert set(rows) == stems, "note_index does not cover every generated note"
        # Preferential attachment: at least one hub note carries incoming links
        # (the graph-retrieval leg needs a linked graph to traverse).
        assert max(inc for inc, _ in rows.values()) >= 2

    def test_real_update_index_cli_accepts_bench_vault(self, tmp_path: Path) -> None:
        """The production update_index CLI runs clean over a generated vault.

        Exercises the CLI surface end-to-end (frontmatter parsing, db write,
        manifests). The generator's bench-local vaults.yaml registry makes the
        SEC-P001 resolver accept the throwaway path; CLAUDE_VAULT pins any
        child resolution (including the embeddings spawn) to the bench vault.
        """
        out = tmp_path / "v6"
        _generate(out, notes=6)
        env = {
            **os.environ,
            "CLAUDE_VAULT": str(out),
            "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        }
        env.pop("PARSIDION_INTERNAL", None)
        proc = subprocess.run(
            [sys.executable, str(UPDATE_INDEX), "--vault", str(out)],
            cwd=str(out),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, f"update_index failed:\n{proc.stderr}"
        assert "parse_warning" not in proc.stdout + proc.stderr
        assert (out / "CLAUDE.md").exists(), "CLI run wrote no vault CLAUDE.md"

    def test_generation_is_deterministic_for_a_seed(self, tmp_path: Path) -> None:
        """Same seed -> identical note paths; the bench is reproducible."""
        runs: list[list[str]] = []
        for name in ("a", "b"):
            out = tmp_path / name
            _generate(out, notes=8, seed=7)
            runs.append(sorted(str(p.relative_to(out)) for p in out.glob("*/*.md")))
        assert runs[0] == runs[1]
