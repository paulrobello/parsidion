"""ENH-003: in-process embedding cache, in-process caller conversion, and the
opt-in persistent embedding service.

These tests pin the three behaviours ENH-003 added:

* ``_get_embedding_model`` caches one fastembed instance per model name per
  process and does not memoise a load failure (Phase 1).
* ``find_related_by_semantic`` calls ``vault_search.search`` in-process and
  forwards ``vault=`` (Phase 2; ARC-027(b) regression guard).
* ``_embeddings_service_active`` gates the persistent service behind an
  explicit opt-in AND a non-parsight backend, and the daemon's request handler
  speaks the newline-JSON protocol (Phase 4).

ENH-020 (default-on lifecycle) adds:

* ``service_enabled`` defaults to true in the schema.
* The client spawn is single-flight across processes (flock) and logs an
  ``EmbedServiceSpawn`` hook event; a service miss logs a debounced
  ``EmbedColdLoad`` event.
* A launcher that loses the daemon bind race does not unlink the winner's
  socket.

fastembed is mocked throughout so the suite runs without the search extra.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import socket
import subprocess
import sys
import threading
import time
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vault_embed_serve  # noqa: E402
import vault_links  # noqa: E402
import vault_search  # noqa: E402
from cli.search import embeddings as cli_embeddings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_model_cache() -> Iterator[None]:
    """Isolate tests from the process-wide lru_cache (before and after)."""
    vault_search._get_embedding_model.cache_clear()
    yield
    vault_search._get_embedding_model.cache_clear()


def _fake_fastembed(monkeypatch: pytest.MonkeyPatch, te_cls: type) -> types.ModuleType:
    fake = types.ModuleType("fastembed")
    fake.TextEmbedding = te_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastembed", fake)
    return fake


# ---------------------------------------------------------------------------
# Phase 1: in-process model cache
# ---------------------------------------------------------------------------


def test_model_cached_per_name(monkeypatch: pytest.MonkeyPatch) -> None:
    instantiations = {"n": 0}

    class FakeTE:
        def __init__(self, model_name: str) -> None:
            instantiations["n"] += 1
            self.model_name = model_name

    _fake_fastembed(monkeypatch, FakeTE)

    a = vault_search._get_embedding_model("bge-small")
    b = vault_search._get_embedding_model("bge-small")
    other = vault_search._get_embedding_model("bge-base")

    assert a is b  # same name -> same instance
    assert other is not a  # different name -> different instance
    assert instantiations["n"] == 2  # constructed once per name, not per call


def test_model_load_failure_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed load must not be memoised — the next call retries."""
    attempts = {"n": 0}

    class BoomTE:
        def __init__(self, model_name: str) -> None:
            attempts["n"] += 1
            raise RuntimeError("model unavailable")

    _fake_fastembed(monkeypatch, BoomTE)

    with pytest.raises(RuntimeError):
        vault_search._get_embedding_model("m")
    with pytest.raises(RuntimeError):
        vault_search._get_embedding_model("m")

    assert attempts["n"] == 2  # lru_cache did not swallow the failure


# ---------------------------------------------------------------------------
# Phase 2: in-process caller + vault forwarding
# ---------------------------------------------------------------------------


def test_find_related_by_semantic_forwards_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "embeddings.db").write_bytes(b"")  # pass the db_path.exists() gate
    note = vault / "some-note.md"

    captured: dict[str, object] = {}

    def fake_search(query: str, **kw: object) -> list[dict[str, object]]:
        captured["query"] = query
        captured["vault"] = kw.get("vault")
        captured["min_score"] = kw.get("min_score")
        return [{"stem": "other-note"}, {"stem": "some-note"}]  # 2nd is self

    monkeypatch.setattr(vault_search, "search", fake_search)

    links = vault_links.find_related_by_semantic(
        new_note_path=note, vault=vault, tag_strs=["x"], max_links=5
    )

    assert captured["vault"] == vault  # ARC-027(b): vault forwarded
    assert captured["min_score"] is not None
    assert links == ["[[other-note]]"]  # self-reference filtered out


# ---------------------------------------------------------------------------
# Phase 4: the opt-in + parsight gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "backend", "expected"),
    [
        (False, "auto", False),
        (True, "auto", True),
        (True, "embeddings", True),
        (True, "parsight", False),  # the user's constraint
        (False, "parsight", False),
    ],
)
def test_service_active_gate(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, backend: str, expected: bool
) -> None:
    orig = cli_embeddings.get_config

    def fake_get(section: str, key: str, default: object = None) -> object:
        if section == "embeddings" and key == "service_enabled":
            return enabled
        if section == "search" and key == "backend":
            return backend
        return orig(section, key, default)

    # ARC-103: ``_embeddings_service_active`` resolves both dependencies as bare
    # names in ``cli.search.embeddings`` globals, so both patches must land
    # there. The former ``vault_search._configured_search_backend`` patch was a
    # no-op on the shim's re-export, which let the backend guard read the real
    # machine config instead of the parametrized value.
    monkeypatch.setattr(cli_embeddings, "get_config", fake_get)
    monkeypatch.setattr(cli_embeddings, "_configured_search_backend", lambda: backend)

    assert vault_search._embeddings_service_active() is expected


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("embeddings", True),  # -B embeddings outranks a parsight config
        ("parsight", False),
        ("auto", True),
        (None, False),  # no override -> the parsight config wins
    ],
)
def test_service_gate_honours_backend_override(
    monkeypatch: pytest.MonkeyPatch, override: str | None, expected: bool
) -> None:
    """ENH-020: a per-call ``-B`` override reaches the service gate.

    With ``search.backend: parsight`` configured, a CLI-forced embeddings run
    is NOT served by parsight, so the warm service must be used; without an
    override the parsight config keeps it off.
    """
    orig = cli_embeddings.get_config

    def fake_get(section: str, key: str, default: object = None) -> object:
        if section == "embeddings" and key == "service_enabled":
            return True
        return orig(section, key, default)

    monkeypatch.setattr(cli_embeddings, "get_config", fake_get)
    monkeypatch.setattr(
        cli_embeddings, "_configured_search_backend", lambda: "parsight"
    )

    assert cli_embeddings._embeddings_service_active(override) is expected


# ---------------------------------------------------------------------------
# Phase 4: daemon request handler (newline-JSON over AF_UNIX)
# ---------------------------------------------------------------------------


class _FakeModel:
    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    def embed(self, texts: list[str]):  # type: ignore[no-untyped-def]
        return [list(self._vec) for _ in texts]


def test_handle_returns_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        vault_embed_serve, "_get_model", lambda name: _FakeModel([0.1, 0.2, 0.3])
    )
    client, peer = socket.socketpair()
    try:
        t = threading.Thread(
            target=vault_embed_serve._handle, args=(peer, "default-model")
        )
        t.start()
        client.sendall((json.dumps({"text": "hi", "model": "m"}) + "\n").encode())
        data = client.recv(4096).decode().strip()
        t.join(timeout=5)
        assert json.loads(data) == {"vector": [0.1, 0.2, 0.3]}
    finally:
        client.close()
        peer.close()


def test_handle_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_name: str) -> None:
        raise RuntimeError("no model")

    monkeypatch.setattr(vault_embed_serve, "_get_model", boom)
    client, peer = socket.socketpair()
    try:
        t = threading.Thread(
            target=vault_embed_serve._handle, args=(peer, "default-model")
        )
        t.start()
        client.sendall((json.dumps({"text": "hi"}) + "\n").encode())
        data = client.recv(4096).decode().strip()
        t.join(timeout=5)
        payload = json.loads(data)
        assert "error" in payload and "no model" in payload["error"]
    finally:
        client.close()
        peer.close()


def test_handle_ignores_client_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-018: the model comes from server config, never the request."""
    seen: list[str] = []

    def fake_get_model(name: str) -> _FakeModel:
        seen.append(name)
        return _FakeModel([0.5])

    monkeypatch.setattr(vault_embed_serve, "_get_model", fake_get_model)
    client, peer = socket.socketpair()
    try:
        t = threading.Thread(
            target=vault_embed_serve._handle, args=(peer, "server-model")
        )
        t.start()
        client.sendall(
            (json.dumps({"text": "hi", "model": "attacker-model"}) + "\n").encode()
        )
        data = client.recv(4096).decode().strip()
        t.join(timeout=5)
        assert json.loads(data) == {"vector": [0.5]}
        assert seen == ["server-model"]
    finally:
        client.close()
        peer.close()


def test_handle_rejects_oversized_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-018: an unterminated request over 64 KiB is refused, not buffered."""
    monkeypatch.setattr(vault_embed_serve, "_get_model", lambda name: _FakeModel([0.1]))
    client, peer = socket.socketpair()
    try:
        t = threading.Thread(
            target=vault_embed_serve._handle, args=(peer, "default-model")
        )
        t.start()
        client.sendall(b'{"text": "' + b"x" * (70 * 1024) + b'"}')
        data = client.recv(4096).decode().strip()
        t.join(timeout=5)
        payload = json.loads(data)
        assert "error" in payload and "64 KiB" in payload["error"]
    finally:
        client.close()
        peer.close()


def test_socket_and_pid_paths_live_outside_vault(tmp_path: Path) -> None:
    vault = tmp_path / "avault"
    sp = vault_embed_serve.socket_path(vault)
    pp = vault_embed_serve.pid_path(vault)
    assert sp.parent == pp.parent == vault_embed_serve.runtime_dir()
    # The IPC artifacts must not live inside the (git-synced) vault tree.
    assert vault != sp
    assert vault not in sp.parents
    assert sp.name.startswith("embed-") and sp.suffix == ".sock"
    assert pp.name.startswith("embed-") and pp.suffix == ".pid"


def test_pid_singleton_helpers_roundtrip(tmp_path: Path) -> None:
    pid_file = tmp_path / "embed.pid"
    assert vault_embed_serve._read_pid(pid_file) is None
    vault_embed_serve._write_pid(pid_file, 4242)
    assert vault_embed_serve._read_pid(pid_file) == 4242
    # PID 4242 is not a real process here -> not alive (stale-reclaim path).
    assert vault_embed_serve.pid_alive(4242) is False


# ---------------------------------------------------------------------------
# PRF-105: bounded per-connection stalls
# ---------------------------------------------------------------------------


def test_connection_timeout_is_short() -> None:
    """A warm embed is ~10 ms and main() preloads the model before bind().

    The old 60 s let one wedged client hold a handler open for a minute; with
    the serial accept loop that stalled every other client for the same minute.
    """
    assert vault_embed_serve._CONN_TIMEOUT_SECS == 5.0


def test_handle_gives_up_on_a_client_that_never_sends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent client is dropped at the timeout, not held indefinitely."""
    monkeypatch.setattr(vault_embed_serve, "_CONN_TIMEOUT_SECS", 0.2)
    monkeypatch.setattr(vault_embed_serve, "_get_model", lambda name: _FakeModel([0.1]))
    client, peer = socket.socketpair()
    try:
        handler = threading.Thread(
            target=vault_embed_serve._handle, args=(peer, "m"), daemon=True
        )
        started = time.monotonic()
        handler.start()
        handler.join(timeout=5)
        assert not handler.is_alive(), "handler did not give up on a silent client"
        assert time.monotonic() - started < 3
    finally:
        client.close()
        peer.close()


def test_accept_loop_dispatches_handlers_on_threads() -> None:
    """main()'s accept loop must hand each connection to a daemon thread.

    Checked structurally: main() installs a SIGTERM handler, which raises off
    the main thread, so the loop itself cannot be driven from a test. Without
    this, the behaviour test below would still pass while the real server went
    back to serving connections serially.
    """
    source = (SCRIPTS_DIR / "vault_embed_serve.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main_fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    threaded = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "Thread"
        and any(
            kw.arg == "target"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "_handle"
            for kw in node.keywords
        )
    ]
    assert len(threaded) == 1, "main() does not dispatch _handle on a thread"
    assert any(
        kw.arg == "daemon" and getattr(kw.value, "value", None) is True
        for kw in threaded[0].keywords
    ), "the handler thread must be a daemon so shutdown is never held up"

    # And the loop must not also call _handle inline.
    inline = [
        node
        for node in ast.walk(main_fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_handle"
    ]
    assert inline == []


def test_a_wedged_client_does_not_block_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Threaded handling means a stalled connection is not head-of-line blocking.

    The wedged handler is left running on its own thread; the second client
    must be served without waiting for the first to time out.
    """
    monkeypatch.setattr(vault_embed_serve, "_CONN_TIMEOUT_SECS", 30.0)
    monkeypatch.setattr(vault_embed_serve, "_get_model", lambda name: _FakeModel([0.7]))
    wedged_client, wedged_peer = socket.socketpair()
    good_client, good_peer = socket.socketpair()
    try:
        # Connected, but never sends a line -- the handler blocks in recv().
        threading.Thread(
            target=vault_embed_serve._handle, args=(wedged_peer, "m"), daemon=True
        ).start()

        started = time.monotonic()
        threading.Thread(
            target=vault_embed_serve._handle, args=(good_peer, "m"), daemon=True
        ).start()
        good_client.sendall((json.dumps({"text": "hi"}) + "\n").encode())
        good_client.settimeout(5)
        payload = json.loads(good_client.recv(4096).decode().strip())

        assert payload == {"vector": [0.7]}
        assert time.monotonic() - started < 5  # not waiting on the wedged peer
    finally:
        for sock in (wedged_client, wedged_peer, good_client, good_peer):
            sock.close()


def test_embed_calls_are_serialised() -> None:
    """Concurrent handlers must not run fastembed inference in parallel."""
    overlaps: list[int] = []
    inflight = 0
    counter_lock = threading.Lock()

    class _SlowModel:
        def embed(self, texts: list[str]):  # type: ignore[no-untyped-def]
            nonlocal inflight
            with counter_lock:
                inflight += 1
                overlaps.append(inflight)
            time.sleep(0.05)
            with counter_lock:
                inflight -= 1
            return [[0.3] for _ in texts]

    model = _SlowModel()
    threads = [
        threading.Thread(target=vault_embed_serve._embed, args=(model, "x"))
        for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert overlaps, "inference never ran"
    assert max(overlaps) == 1, f"concurrent inference observed: {overlaps}"


# ---------------------------------------------------------------------------
# ENH-020: default-on lifecycle — single-flight spawn, events, cleanup race
# ---------------------------------------------------------------------------


def test_schema_defaults_service_enabled_true() -> None:
    """The ENH-020 flip: the schema default (what get_config falls back to)
    must be true, so the service auto-starts with no config.yaml entry."""
    from core.vault_schema import EmbeddingsConfig

    field = next(
        f for f in dataclasses.fields(EmbeddingsConfig) if f.name == "service_enabled"
    )
    assert field.default is True


def test_spawn_service_single_flight_cross_process(
    tmp_path: Path,
) -> None:
    """Two concurrent cold clients spawn exactly one service.

    flock is per-process, so the contention path needs two real processes:
    each one stubs Popen to hold the spawn window open (simulating the daemon
    launch), calls ``_spawn_service``, and reports how many times it spawned.
    Without the single-flight lock both processes spawn (total 2); with it
    the loser sees the lock held and skips (total 1).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    probe = (
        "import sys, time\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(SCRIPTS_DIR)!r})\n"
        "from cli.search import embeddings as e\n"
        "spawns = []\n"
        "class _Proc:\n"
        "    pid = 4242\n"
        "def _fake_popen(*a, **k):\n"
        "    time.sleep(0.5)\n"
        "    spawns.append(1)\n"
        "    return _Proc()\n"
        "e.subprocess.Popen = _fake_popen\n"
        "e.write_hook_event = lambda *a, **k: None\n"
        "e._spawn_service(Path(sys.argv[1]), 'm')\n"
        "print(len(spawns))\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", probe, str(vault)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = []
    for proc in procs:
        out, err = proc.communicate(timeout=30)
        assert proc.returncode == 0, f"probe failed: {err}"
        outputs.append(out.strip())

    total = sum(int(n) for n in outputs)
    assert total == 1, f"concurrent cold clients spawned {total} services, not 1"


def test_spawn_service_writes_spawn_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful spawn logs EmbedServiceSpawn with the daemon PID."""
    vault = tmp_path / "vault"
    vault.mkdir()
    events: list[tuple[str, dict[str, object]]] = []

    class _Proc:
        pid = 777

    monkeypatch.setattr(cli_embeddings.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(
        cli_embeddings,
        "write_hook_event",
        lambda hook, project, duration_ms, vault=None, **kw: events.append(
            (hook, dict(kw))
        ),
    )
    monkeypatch.setattr(cli_embeddings, "_last_service_spawn_attempt", 0.0)

    cli_embeddings._spawn_service(vault, "model-x")

    assert len(events) == 1
    hook, extra = events[0]
    assert hook == "EmbedServiceSpawn"
    assert extra["pid"] == 777
    assert extra["model"] == "model-x"


def test_cold_load_event_on_service_miss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A service miss falls back in-process and logs a debounced EmbedColdLoad."""
    vault = tmp_path / "vault"
    vault.mkdir()
    events: list[str] = []

    monkeypatch.setattr(
        cli_embeddings, "_embeddings_service_active", lambda backend=None: True
    )
    monkeypatch.setattr(cli_embeddings, "_spawn_service", lambda v, m: None)
    monkeypatch.setattr(
        cli_embeddings, "_service_embed", lambda q, m, v, timeout=5.0: None
    )
    monkeypatch.setattr(
        cli_embeddings,
        "write_hook_event",
        lambda hook, project, duration_ms, vault=None, **kw: events.append(hook),
    )
    monkeypatch.setattr(
        cli_embeddings, "_get_embedding_model", lambda name: _FakeModel([0.9])
    )
    monkeypatch.setattr(cli_embeddings, "_last_cold_load_event", 0.0)

    vec = cli_embeddings._embed_query("q", "m", vault)
    assert vec == [0.9]  # in-process fallback served the query
    assert events == ["EmbedColdLoad"]

    # Debounced: a second miss inside the window logs nothing more.
    cli_embeddings._embed_query("q", "m", vault)
    assert events == ["EmbedColdLoad"]


def test_lock_path_lives_outside_vault(tmp_path: Path) -> None:
    """The single-flight lock follows the socket/pid rule: runtime dir, never
    inside the git-synced vault tree."""
    lp = vault_embed_serve.lock_path(tmp_path / "avault")
    assert lp.parent == vault_embed_serve.runtime_dir()
    assert lp.name.startswith("embed-") and lp.suffix == ".lock"


def test_cleanup_loser_does_not_unlink_winner_socket(tmp_path: Path) -> None:
    """A launcher that lost the bind race must leave the winner's socket alone."""
    sock = tmp_path / "embed-x.sock"
    sock.write_text("", encoding="utf-8")
    pid_file = tmp_path / "embed-x.pid"
    pid_file.write_text("999", encoding="utf-8")  # not our pid

    vault_embed_serve._cleanup(sock, pid_file, our_pid=111, owns_socket=False)
    assert sock.exists(), "bind-race loser unlinked the winner's socket"

    vault_embed_serve._cleanup(sock, pid_file, our_pid=111, owns_socket=True)
    assert not sock.exists()
