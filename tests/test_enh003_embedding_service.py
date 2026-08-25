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

fastembed is mocked throughout so the suite runs without the search extra.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
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
import vault_common  # noqa: E402


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
    orig = vault_common.get_config

    def fake_get(section: str, key: str, default: object = None) -> object:
        if section == "embeddings" and key == "service_enabled":
            return enabled
        if section == "search" and key == "backend":
            return backend
        return orig(section, key, default)

    monkeypatch.setattr(vault_common, "get_config", fake_get)
    monkeypatch.setattr(vault_search, "_configured_search_backend", lambda: backend)

    assert vault_search._embeddings_service_active() is expected


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
