"""Bounded evidence for the blocking call immediately after ``[links] Stripped``.

Repro (only in a coordinated low-load window):
    PARSIDION_RUN_COLD_MODEL=1 uv run --with fastembed --with sqlite-vec pytest
    tests/test_semantic_backlink_cold_load.py -q -s

2026-10-25 findings: real model timings were NOT measured on the development
host (load observed at 164.49, 189.80 and coordinator-confirmed 265.75).
The deterministic child test instead captures the blocking call stack without
loading fastembed weights. Idempotence is covered separately in
``test_summarizer_links_idempotence.py``.

Findings: summarizer.pipeline._apply_backlinks_and_strip_links calls
core.vault_links.find_related_by_semantic immediately after stripping links.
That imports vault_search, routes to cli.search.embeddings._search_embeddings,
and (with the service disabled) calls _get_embedding_model before model.embed.
The probe times those real operations in a fresh interpreter, not fake models.
This establishes the reachable blocking path, not the cause of a historical
poll/select hang. A first process load can include cache lookup/download and
ONNX initialization. An absent optional extra or overloaded host is a documented
skip, never a successful measurement. The worker has a 180-second deadline.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

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
# Deterministic variant (coordinator-approved): a fake model that blocks
# proves the exact stage boundary and the missing timeout coverage without
# loading the real ONNX weights. Runs under plain pytest.
# ---------------------------------------------------------------------------

_BLOCKING_WORKER = r"""
import faulthandler
import signal
import sqlite3
import struct
import sys
import threading
import time
import types
from pathlib import Path

vault = Path(sys.argv[1])
sys.path.insert(0, sys.argv[2])
sys.modules.setdefault(
    "anyio",
    types.SimpleNamespace(
        Semaphore=object,
        to_thread=types.SimpleNamespace(run_sync=lambda func, *a: func(*a)),
    ),
)

from cli.search import embeddings
from summarizer.pipeline import _apply_backlinks_and_strip_links

_release = threading.Event()


class _BlockingModel:
    def embed(self, queries):
        _release.wait(timeout=50)  # bounded watcher; parent terminates sooner
        return [[0.0] * 384 for _ in queries]


embeddings._get_embedding_model = lambda model_name: _BlockingModel()

faulthandler.register(signal.SIGUSR1, all_threads=True)
print("CHILD_READY", flush=True)

new_note = vault / "new-note.md"
notes = [new_note, vault / "related-note.md"]
_apply_backlinks_and_strip_links(new_note, vault, notes)
print("STAGE_RETURNED", flush=True)
"""


@pytest.mark.timeout(90)
def test_semantic_backlink_blocks_past_configured_timeout_with_captured_stack(
    tmp_vault: Path,
) -> None:
    """The stage after ``[links] Stripped`` blocks unbounded in one call.

    The child runs the production stage with ``summarizer.ai_timeout: 2``
    configured. A SIGUSR1 stack dump captured while blocked must show the
    exact frame chain -- strip already finished, blocked inside
    ``find_related_by_semantic`` -> ``vault_search.search`` ->
    ``_search_embeddings`` -> ``_embed_query`` -> model ``embed`` -- and the
    child must still be alive ~5s in, past the configured timeout that never
    applies to this path (the ``embeddings`` config section has no timeout
    key at all).
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

    scripts = Path(__file__).resolve().parents[1] / "skills/parsidion/scripts"
    proc = subprocess.Popen(
        [sys.executable, "-c", _BLOCKING_WORKER, str(tmp_vault), str(scripts)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready = proc.stdout.readline() if proc.stdout else ""
        assert ready.strip() == "CHILD_READY", proc.poll()
        # ~4s after the stage starts: past the configured ai_timeout of 2s,
        # while the fake model holds the call open.
        time.sleep(4.0)
        proc.send_signal(signal.SIGUSR1)
        time.sleep(1.5)
        assert proc.poll() is None, "stage returned before the timeout proof window"
    finally:
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=15)

    assert "STAGE_RETURNED" not in stdout
    assert "[links] Stripped 1 non-resolving wikilink(s)" in stderr
    for frame in (
        "in _apply_backlinks_and_strip_links",
        "in find_related_by_semantic",
        "in search",
        "in _search_embeddings",
        "in _embed_query",
        "in embed",
    ):
        assert frame in stderr, f"missing stack frame {frame!r} in:\n{stderr}"
