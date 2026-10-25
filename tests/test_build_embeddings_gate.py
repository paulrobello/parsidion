"""The embeddings.enabled gate in ``build_embeddings.py``.

The vault post-merge hook (and manual runs) call build_embeddings.py
directly, bypassing update_index.py's own enabled check — a vault with
``embeddings.enabled: false`` in its config still rebuilt a 160 MB
embeddings.db after every pull.  The gate inside ``active_vault_scope``
must short-circuit such runs before any model work, with ``--force`` as
the explicit override.

build_embeddings.py imports ``fastembed`` at module top level, and the
test environment intentionally lacks the ``search`` extra — so each case
spawns the script as a real subprocess with a stub ``fastembed`` package
on PYTHONPATH (mirroring the poisoned-subprocess pattern of
test_build_embeddings_imports.py).  The gate fires before the stub is
ever used.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = (
    Path(__file__).resolve().parent.parent / "skills" / "parsidion" / "scripts"
)
_SCRIPT = _SCRIPTS_DIR / "build_embeddings.py"

_SKIP_MSG = "skipping vector build"


def _make_stub_env(tmp_path: Path) -> dict[str, str]:
    """Return an env with a stub fastembed package importable."""
    stub = tmp_path / "stubs" / "fastembed"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text(
        "class TextEmbedding:\n"
        "    def __init__(self, model_name=None):\n"
        "        self.model_name = model_name\n"
        "    def embed(self, texts):\n"
        "        return iter([[0.0] * 384 for _ in texts])\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path / "stubs") + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _make_vault(tmp_path: Path, enabled: bool) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "config.yaml").write_text(
        "embeddings:\n  enabled: true\n"
        if enabled
        else "embeddings:\n  enabled: false\n"
    )
    # SEC-P001: the resolver is allowlist-based — CLAUDE_VAULT references are
    # accepted only when registered in vaults.yaml (same pattern as the
    # tmp_vault fixture in tests/conftest.py).
    cfg_dir = tmp_path / ".config" / "parsidion"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "vaults.yaml").write_text(f"vaults:\n  test: {vault}\n")
    return vault


def _run(vault: Path, *flags: str) -> subprocess.CompletedProcess:
    env = _make_stub_env(vault.parent)
    env["CLAUDE_VAULT"] = str(vault)
    env["XDG_CONFIG_HOME"] = str(vault.parent / ".config")
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *flags],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(_SCRIPTS_DIR),
        timeout=120,
    )


def test_disabled_vault_skips_build(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path, enabled=False)
    proc = _run(vault)
    assert proc.returncode == 0, proc.stderr
    assert _SKIP_MSG in proc.stdout
    # The gate must fire before any vector work: no embeddings.db written.
    assert not (vault / "embeddings.db").exists()


def test_force_overrides_disabled_vault(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path, enabled=False)
    proc = _run(vault, "--force", "--dry-run")
    # --force proceeds past the gate; with a stub model the run may fail
    # later, but it must NOT take the disabled short-circuit.
    assert _SKIP_MSG not in proc.stdout


def test_enabled_vault_is_untouched(tmp_path: Path) -> None:
    vault = _make_vault(tmp_path, enabled=True)
    proc = _run(vault, "--dry-run")
    assert _SKIP_MSG not in proc.stdout
