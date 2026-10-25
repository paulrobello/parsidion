"""Bounded evidence for the blocking call immediately after ``[links] Stripped``.

Repro (only in a coordinated low-load window):
    PARSIDION_RUN_COLD_MODEL=1 uv run --with fastembed --with sqlite-vec pytest
    tests/test_semantic_backlink_cold_load.py -q -s

2026-10-25 findings: real model timings were NOT measured on the development
host (load observed at 164.49, 189.80 and coordinator-confirmed 265.75).
The deterministic child test instead captures the blocking call stack without
loading fastembed weights. Idempotence is covered separately in
``test_summarizer_links_idempotence.py``.

Findings (pre-fix): summarizer.pipeline._apply_backlinks_and_strip_links called
core.vault_links.find_related_by_semantic in-process immediately after
stripping links. That imported vault_search, routed to
cli.search.embeddings._search_embeddings, and (with the service disabled)
called _get_embedding_model before model.embed -- an unbounded in-process
model load and inference that no summarizer timeout could stop.

Fixed 2026-10-25: the links stage now runs the optional semantic lookup in a
short-lived child process via core.subproc_util.run_with_pgkill (explicit
deadline, SIGTERM->SIGKILL process-group cleanup), falling back to tag links
on timeout/launch/invalid results and skipping the child entirely when
embeddings are disabled or no index exists. The tests below cover the killed
stalled child, normal-result parity, the disabled path, and the no-in-process-
model-load contract. The cold-load probe above still times the real lookup
operations in a fresh interpreter when explicitly enabled.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# Plain pytest runs lack the entry shim's anyio dependency; summarizer.pipeline
# imports it at module level (annotation-only use). Same stub pattern as
# test_summarizer_links_idempotence.py.
if "anyio" not in sys.modules and importlib.util.find_spec("anyio") is None:
    sys.modules["anyio"] = types.SimpleNamespace(  # type: ignore[assignment]
        Semaphore=object,
        to_thread=types.SimpleNamespace(run_sync=lambda func, *a: func(*a)),
    )

_WORKER = r"""
import builtins
import json
import os
from pathlib import Path
import sqlite3
import struct
import sys
import time

vault = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
from core import vault_links
from core.vault_config import clear_config_cache
clear_config_cache()
# No build_embeddings import: it would pre-import fastembed and invalidate coldness.
conn = sqlite3.connect(vault / "embeddings.db")
conn.execute("CREATE TABLE note_embeddings (stem TEXT, path TEXT, folder TEXT, "
             "title TEXT, tags TEXT, embedding BLOB, mtime REAL)")
conn.execute("INSERT INTO note_embeddings VALUES (?, ?, ?, ?, ?, ?, ?)",
             ("related-note", str(vault / "related-note.md"), "", "Related", "link",
              struct.pack("384f", *([1.0] * 384)), time.time()))
conn.commit()
conn.close()

metrics = {}
real_import = builtins.__import__

def measured_import(name, *args, **kwargs):
    if name != "vault_search":
        return real_import(name, *args, **kwargs)
    start = time.perf_counter()
    module = real_import(name, *args, **kwargs)
    metrics["import_vault_search_s"] = time.perf_counter() - start
    from cli.search import embeddings
    original_load = embeddings._get_embedding_model
    assert original_load.cache_info().currsize == 0

    def measured_load(model_name):
        start = time.perf_counter()
        model = original_load(model_name)
        metrics["first_model_load_s"] = time.perf_counter() - start
        metrics["model_name"] = model_name
        return model

    embeddings._get_embedding_model = measured_load
    original_embed = embeddings._embed_query

    def measured_embed(*args, **kwargs):
        start = time.perf_counter()
        vector = original_embed(*args, **kwargs)
        metrics["first_embedding_s"] = time.perf_counter() - start
        # A real model vector makes the fixture's related note an exact match.
        conn = sqlite3.connect(vault / "embeddings.db")
        conn.execute("UPDATE note_embeddings SET embedding = ?",
                     (struct.pack(f"{len(vector)}f", *vector),))
        conn.commit()
        conn.close()
        return vector

    embeddings._embed_query = measured_embed
    return module

assert "vault_search" not in sys.modules
builtins.__import__ = measured_import
metrics["load_before"] = os.getloadavg()[0]
start = time.perf_counter()
links = vault_links.find_related_by_semantic(vault / "new-note.md", vault,
                                            tag_strs=["link"])
metrics["semantic_call_s"] = time.perf_counter() - start
metrics["load_after"] = os.getloadavg()[0]
metrics["links"] = links
print("COLD_LOAD_EVIDENCE=" + json.dumps(metrics), flush=True)
assert "first_model_load_s" in metrics, "real model loader was not reached"
assert links == ["[[related-note]]"], links
"""


@pytest.mark.timeout(200)
def test_real_semantic_backlink_cold_load(tmp_vault: Path) -> None:
    pytest.importorskip("fastembed")
    pytest.importorskip("sqlite_vec")
    if os.environ.get("PARSIDION_RUN_COLD_MODEL") != "1":
        pytest.skip(
            "real model probe requires a coordinated low-load window and PARSIDION_RUN_COLD_MODEL=1"
        )
    load = os.getloadavg()[0]
    if load > 15:
        pytest.skip(f"cold-load timing requires host load <=15, actual {load:.2f}")
    (tmp_vault / "config.yaml").write_text(
        "search:\n  backend: embeddings\nembeddings:\n  enabled: true\n"
        "  service_enabled: false\n  decay_enabled: false\n",
        encoding="utf-8",
    )
    for stem in ("new-note", "related-note"):
        (tmp_vault / f"{stem}.md").write_text(
            "---\ntags: [link]\nrelated: []\n---\n# Link evidence\n",
            encoding="utf-8",
        )
    scripts = Path(__file__).resolve().parents[1] / "skills/parsidion/scripts"
    result = subprocess.run(
        [sys.executable, "-c", _WORKER, str(tmp_vault), str(scripts)],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    print(result.stdout, end="")
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout.split("COLD_LOAD_EVIDENCE=", 1)[1])
    if max(evidence["load_before"], evidence["load_after"]) > 15:
        pytest.skip(f"discard overloaded timing sample: {evidence}")
    assert 0 < evidence["first_model_load_s"] <= evidence["first_embedding_s"]
    assert evidence["first_embedding_s"] <= evidence["semantic_call_s"]
    assert evidence["import_vault_search_s"] > 0


# ---------------------------------------------------------------------------
# Deterministic variant: the links stage is now bounded. The semantic lookup
# runs in a short-lived grandchild with an explicit deadline, so the stage
# returns even when a semantic index exists and the model stack is unavailable
# (or wedged) -- and it never loads the embedding model in-process.
# ---------------------------------------------------------------------------

_BOUNDED_WORKER = r"""
import sys
import types
from pathlib import Path

sys.path.insert(0, sys.argv[2])
sys.modules.setdefault(
    "anyio",
    types.SimpleNamespace(
        Semaphore=object,
        to_thread=types.SimpleNamespace(run_sync=lambda func, *a: func(*a)),
    ),
)

from summarizer.pipeline import _apply_backlinks_and_strip_links

vault = Path(sys.argv[1])
note = vault / "new-note.md"
_apply_backlinks_and_strip_links(note, vault, [note, vault / "related-note.md"])
print("VAULT_SEARCH_LOADED", "vault_search" in sys.modules, flush=True)
print("STAGE_RETURNED", flush=True)
"""


@pytest.mark.timeout(90)
def test_links_stage_bounded_and_no_in_process_model_load(tmp_vault: Path) -> None:
    """The post-``[links] Stripped`` stage returns instead of blocking.

    Pre-fix this test proved the opposite (SIGUSR1 stack pinned the block
    inside in-process ``model.embed`` past ``summarizer.ai_timeout``). The
    bounded path moved that lookup into a killable child, so the contract is
    now: the stage completes, the tag fallback links the related note, and
    the parent process never imports the embedding stack at all.
    """
    (tmp_vault / "config.yaml").write_text(
        "search:\n  backend: embeddings\n"
        "summarizer:\n  ai_timeout: 2\n"
        "embeddings:\n  enabled: true\n  service_enabled: false\n",
        encoding="utf-8",
    )
    (tmp_vault / "new-note.md").write_text(
        '---\ntags: [link]\nrelated: ["[[invented-hub]]", "[[related-note]]"]\n'
        "---\n# New\n",
        encoding="utf-8",
    )
    (tmp_vault / "related-note.md").write_text(
        "---\ntags: [link]\nrelated: []\n---\n# Related\n", encoding="utf-8"
    )
    conn = sqlite3.connect(tmp_vault / "embeddings.db")
    conn.execute(
        "CREATE TABLE note_embeddings (stem TEXT, path TEXT, folder TEXT, "
        "title TEXT, tags TEXT, embedding BLOB, mtime REAL)"
    )
    conn.execute(
        "INSERT INTO note_embeddings VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "related-note",
            str(tmp_vault / "related-note.md"),
            "",
            "Related",
            "link",
            struct.pack("384f", *([0.02] * 384)),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()

    result = subprocess.run(
        [sys.executable, "-c", _BOUNDED_WORKER, str(tmp_vault), str(_SCRIPTS_DIR)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "STAGE_RETURNED" in result.stdout
    # The parent (this child) never loaded the embedding stack in-process.
    assert "VAULT_SEARCH_LOADED False" in result.stdout
    # The semantic stage announced itself, then the tag fallback linked the note.
    assert "Semantic lookup (timeout 2s)" in result.stdout
    assert "[links] Stripped 1 non-resolving wikilink(s)" in result.stderr
    related = (tmp_vault / "new-note.md").read_text(encoding="utf-8")
    assert '"[[related-note]]"' in related


_STALLED_WORKER = (
    "import json, os, signal, sys, time\n"
    "from pathlib import Path\n"
    "payload = json.load(sys.stdin)\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "Path(payload['vault'], 'child.pid').write_text(str(os.getpid()))\n"
    "time.sleep(300)\n"
)


def _prepare_vault_with_index(tmp_vault: Path) -> Path:
    (tmp_vault / "config.yaml").write_text(
        "search:\n  backend: embeddings\nembeddings:\n  enabled: true\n"
        "  service_enabled: false\nsummarizer:\n  ai_timeout: 180\n",
        encoding="utf-8",
    )
    (tmp_vault / "new-note.md").write_text(
        "---\ntags: [link]\nrelated: []\n---\n# New\n", encoding="utf-8"
    )
    (tmp_vault / "related-note.md").write_text(
        "---\ntags: [link]\nrelated: []\n---\n# Related\n", encoding="utf-8"
    )
    conn = sqlite3.connect(tmp_vault / "embeddings.db")
    conn.execute(
        "CREATE TABLE note_embeddings (stem TEXT, path TEXT, folder TEXT, "
        "title TEXT, tags TEXT, embedding BLOB, mtime REAL)"
    )
    conn.commit()
    conn.close()
    return tmp_vault / "new-note.md"


def test_stalled_semantic_child_is_killed_and_stage_returns(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A deliberately stalled child is killed at the deadline; the stage returns.

    The stubbed worker child ignores SIGTERM and sleeps 300s — a worst-case
    wedged model load that a plain kill() escalation could leave behind. The
    bounded path must SIGKILL it at the deadline and fall back to tag links.
    """
    import summarizer.pipeline as pipeline

    note = _prepare_vault_with_index(tmp_vault)
    monkeypatch.setattr(pipeline, "_SEMANTIC_BACKLINK_TIMEOUT", 1.0)
    monkeypatch.setattr(pipeline, "_SEMANTIC_BACKLINK_WORKER", _STALLED_WORKER)

    start = time.monotonic()
    pipeline._apply_backlinks_and_strip_links(
        note, tmp_vault, [note, tmp_vault / "related-note.md"]
    )
    elapsed = time.monotonic() - start

    assert elapsed < 10, f"stage blocked {elapsed:.1f}s past the 1s deadline"
    out = capsys.readouterr().out
    assert "Semantic lookup skipped (timeout); using tags" in out
    child_pid_file = tmp_vault / "child.pid"
    assert child_pid_file.exists(), "semantic subprocess was never spawned"
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert child_pid != os.getpid(), "child inherited the test process"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except OSError:
            break
        time.sleep(0.1)
    else:
        pytest.fail(f"stalled child pid {child_pid} survived the deadline")
    related = (tmp_vault / "new-note.md").read_text(encoding="utf-8")
    assert '"[[related-note]]"' in related


def test_bounded_semantic_child_normal_result_parity(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A well-behaved child result reaches the note exactly as before."""
    import summarizer.pipeline as pipeline

    note = _prepare_vault_with_index(tmp_vault)
    captured: dict[str, object] = {}

    def fake_run_with_pgkill(cmd, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        captured["timeout"] = kwargs.get("timeout")
        return (
            "ok",
            subprocess.CompletedProcess(
                cmd, 0, stdout='["[[related-note]]"]\n', stderr=""
            ),
        )

    monkeypatch.setattr(pipeline, "run_with_pgkill", fake_run_with_pgkill)

    pipeline._apply_backlinks_and_strip_links(
        note, tmp_vault, [note, tmp_vault / "related-note.md"]
    )
    out = capsys.readouterr().out
    assert "Semantic lookup (timeout 15s)" in out
    assert "skipped" not in out
    payload = json.loads(str(captured["stdin"]))
    assert payload["tags"] == ["link"]
    assert Path(payload["note"]).name == "new-note.md"
    assert captured["timeout"] == 15.0
    related = (tmp_vault / "new-note.md").read_text(encoding="utf-8")
    assert '"[[related-note]]"' in related


def test_disabled_embeddings_skips_semantic_model_work(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """embeddings.enabled=false (or a missing DB) must spawn no child."""
    import summarizer.pipeline as pipeline

    (tmp_vault / "config.yaml").write_text(
        "embeddings:\n  enabled: false\n", encoding="utf-8"
    )
    (tmp_vault / "new-note.md").write_text(
        "---\ntags: [link]\nrelated: []\n---\n# New\n", encoding="utf-8"
    )
    (tmp_vault / "related-note.md").write_text(
        "---\ntags: [link]\nrelated: []\n---\n# Related\n", encoding="utf-8"
    )

    def explode(*_args, **_kwargs):
        raise AssertionError("no subprocess may run when embeddings are disabled")

    monkeypatch.setattr(pipeline, "run_with_pgkill", explode)

    note = tmp_vault / "new-note.md"
    pipeline._apply_backlinks_and_strip_links(
        note, tmp_vault, [note, tmp_vault / "related-note.md"]
    )
    out = capsys.readouterr().out
    assert "Semantic lookup skipped (disabled or no index); using tags" in out
    related = (tmp_vault / "new-note.md").read_text(encoding="utf-8")
    assert '"[[related-note]]"' in related
