# parsidion-mcp Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `parsidion-mcp`, an MCP server exposing Parsidion vault operations (search, read, write, context, index rebuild, doctor) to Claude Desktop via FastMCP.

**Architecture:** Independent Python package in `parsidion/parsidion-mcp/`. Tool logic lives in focused modules under `tools/`; `server.py` wires them into a FastMCP instance. Heavy operations (rebuild_index, vault_doctor) delegate to existing scripts via subprocess; vault_read/write/context/search use `vault_common` and `vault_search` directly via editable path dependency.

**Tech Stack:** Python 3.13, FastMCP 2.x, vault_common (stdlib), vault_search (fastembed + sqlite-vec), pytest, ruff, pyright.

**Spec:** `docs/superpowers/specs/2026-03-16-parsidion-mcp-design.md`

---

## Chunk 1: Package scaffold + vault_read + vault_write

### Task 1: Create package scaffold

**Files:**
- Create: `parsidion-mcp/pyproject.toml`
- Create: `parsidion-mcp/Makefile`
- Create: `parsidion-mcp/src/parsidion_mcp/__init__.py`
- Create: `parsidion-mcp/src/parsidion_mcp/tools/__init__.py`
- Create: `parsidion-mcp/tests/__init__.py`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p parsidion-mcp/src/parsidion_mcp/tools
mkdir -p parsidion-mcp/tests
```

- [ ] **Step 2: Write `parsidion-mcp/pyproject.toml`**

```toml
[project]
name = "parsidion-mcp"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=2.0",
    "parsidion[search]",
]

[tool.uv.sources]
parsidion = { path = "../", editable = true }

[project.scripts]
parsidion-mcp = "parsidion_mcp.server:main"

[dependency-groups]
dev = [
    "pytest>=9.0",
    "pytest-timeout>=0.5",
    "pytest-cov>=4.0",
    "ruff>=0.9",
    "pyright>=1.1",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-v --cov=src --cov-report=term-missing"
timeout = 10

[tool.ruff]
target-version = "py313"

[tool.ruff.lint]
select = ["E", "W", "F", "B", "BLE", "UP"]
ignore = ["E501", "B008", "B904"]

[tool.pyright]
include = ["src"]
pythonVersion = "3.13"
```

- [ ] **Step 3: Write `parsidion-mcp/Makefile`**

```makefile
.PHONY: install lint fmt typecheck test checkall build

build:
	@echo "No build step needed — editable install only."

install:
	uv tool install --editable .

fmt:
	uv run ruff format .

lint:
	uv run ruff check --fix .

typecheck:
	uv run pyright .

test:
	uv run pytest

checkall: fmt lint typecheck test
```

- [ ] **Step 4: Write empty `__init__.py` files**

`parsidion-mcp/src/parsidion_mcp/__init__.py`:
```python
"""parsidion-mcp: MCP server exposing Parsidion vault to Claude Desktop."""
```

`parsidion-mcp/src/parsidion_mcp/tools/__init__.py`:
```python
"""Tool modules for parsidion-mcp."""
```

`parsidion-mcp/tests/__init__.py`:
```python
```

- [ ] **Step 5: Verify package installs**

```bash
cd parsidion-mcp
uv sync
```

Expected: dependencies resolve, no errors. `vault_common` and `vault_search` importable:
```bash
uv run python -c "import vault_common; import vault_search; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: Commit scaffold**

```bash
git add parsidion-mcp/
git commit -m "feat(mcp): scaffold parsidion-mcp package"
```

---

### Task 2: vault_read and vault_write (TDD)

**Files:**
- Create: `parsidion-mcp/src/parsidion_mcp/tools/notes.py`
- Create: `parsidion-mcp/tests/test_notes.py`

- [ ] **Step 1: Write failing tests**

`parsidion-mcp/tests/test_notes.py`:
```python
"""Tests for vault_read and vault_write tools."""

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from parsidion_mcp.tools.notes import vault_read, vault_write


# ---------------------------------------------------------------------------
# vault_read
# ---------------------------------------------------------------------------

def test_vault_read_returns_content(tmp_path: Path) -> None:
    note = tmp_path / "Patterns" / "my-note.md"
    note.parent.mkdir()
    note.write_text("---\ndate: 2026-01-01\n---\n\n# My Note\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("Patterns/my-note.md")

    assert "# My Note" in result


def test_vault_read_absolute_path(tmp_path: Path) -> None:
    note = tmp_path / "test.md"
    note.write_text("content", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read(str(note))

    assert result == "content"


def test_vault_read_path_escape_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("../../etc/passwd")

    assert result.startswith("ERROR: path escapes vault root")


def test_vault_read_missing_file_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_read("nonexistent.md")

    assert result.startswith("ERROR:")


def test_vault_read_missing_vault_returns_error(tmp_path: Path) -> None:
    absent = tmp_path / "NoVault"

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = absent
        result = vault_read("note.md")

    assert "vault root not found" in result


# ---------------------------------------------------------------------------
# vault_write
# ---------------------------------------------------------------------------

def test_vault_write_creates_file(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_write("new-note.md", "# Hello\n")

    written = tmp_path / "new-note.md"
    assert written.exists()
    assert written.read_text(encoding="utf-8") == "# Hello\n"
    assert str(written) in result


def test_vault_write_creates_parent_dirs(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        vault_write("Patterns/deep/note.md", "content")

    assert (tmp_path / "Patterns" / "deep" / "note.md").exists()


def test_vault_write_overwrites_existing(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("old", encoding="utf-8")

    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        vault_write("note.md", "new")

    assert note.read_text(encoding="utf-8") == "new"


def test_vault_write_path_escape_returns_error(tmp_path: Path) -> None:
    with patch("parsidion_mcp.tools.notes.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        result = vault_write("../../evil.md", "content")

    assert result.startswith("ERROR: path escapes vault root")
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
cd parsidion-mcp
uv run pytest tests/test_notes.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `parsidion_mcp.tools.notes` doesn't exist yet.

- [ ] **Step 3: Implement `notes.py`**

`parsidion-mcp/src/parsidion_mcp/tools/notes.py`:
```python
"""vault_read and vault_write MCP tools."""

from pathlib import Path

import vault_common


def _resolve_vault_path(path: str) -> Path:
    """Resolve *path* against vault root; raise ValueError if it escapes.

    Args:
        path: Path string, relative to vault root or absolute.

    Returns:
        Resolved absolute Path inside vault root.

    Raises:
        ValueError: If the resolved path escapes the vault root.
    """
    vault_root = vault_common.VAULT_ROOT.resolve()
    raw = Path(path)
    candidate = (raw if raw.is_absolute() else vault_root / raw).resolve()
    if not candidate.is_relative_to(vault_root):
        raise ValueError("path escapes vault root")
    return candidate


def vault_read(path: str) -> str:
    """Read a vault note by path.

    Args:
        path: Path relative to vault root (e.g. ``Patterns/my-note.md``) or absolute.

    Returns:
        Full note content (frontmatter + body), or an ERROR string on failure.
    """
    vault_root = vault_common.VAULT_ROOT
    if not vault_root.exists():
        return f"ERROR: vault root not found at {vault_root}"
    try:
        resolved = _resolve_vault_path(path)
        return resolved.read_text(encoding="utf-8")
    except ValueError as exc:
        return f"ERROR: {exc}"
    except FileNotFoundError:
        return f"ERROR: note not found at {path}"
    except OSError as exc:
        return f"ERROR: {exc}"


def vault_write(path: str, content: str) -> str:
    """Create or overwrite a vault note.

    Args:
        path: Path relative to vault root.
        content: Full note content including YAML frontmatter.

    Returns:
        Success message with absolute path, or an ERROR string on failure.
    """
    try:
        resolved = _resolve_vault_path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return f"Written: {resolved}"
    except ValueError as exc:
        return f"ERROR: {exc}"
    except OSError as exc:
        return f"ERROR: {exc}"
```

- [ ] **Step 4: Run tests — verify they all pass**

```bash
uv run pytest tests/test_notes.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parsidion-mcp/src/parsidion_mcp/tools/notes.py parsidion-mcp/tests/test_notes.py
git commit -m "feat(mcp): add vault_read and vault_write tools"
```

---

## Chunk 2: vault_search + vault_context

### Task 3: vault_search (TDD)

**Files:**
- Create: `parsidion-mcp/src/parsidion_mcp/tools/search.py`
- Create: `parsidion-mcp/tests/test_search.py`

- [ ] **Step 1: Write failing tests**

`parsidion-mcp/tests/test_search.py`:
```python
"""Tests for vault_search tool."""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from parsidion_mcp.tools.search import vault_search

_FAKE_NOTE = {
    "score": 0.85,
    "stem": "my-note",
    "title": "My Note",
    "folder": "Patterns",
    "tags": ["python", "pattern"],
    "path": "/vault/Patterns/my-note.md",
    "summary": "",
    "note_type": "pattern",
    "project": "",
    "confidence": "high",
    "mtime": 1700000000.0,
    "related": [],
    "is_stale": False,
    "incoming_links": 2,
}


# ---------------------------------------------------------------------------
# Semantic mode
# ---------------------------------------------------------------------------

def test_semantic_search_returns_json(tmp_path: Path) -> None:
    db = tmp_path / "embeddings.db"
    db.touch()

    with (
        patch("parsidion_mcp.tools.search.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs,
    ):
        mock_vc.get_embeddings_db_path.return_value = db
        mock_vs.search.return_value = [_FAKE_NOTE]

        result = vault_search(query="python patterns")

    parsed = json.loads(result)
    assert len(parsed) == 1
    assert parsed[0]["stem"] == "my-note"
    mock_vs.search.assert_called_once_with("python patterns", top=10, min_score=0.35)


def test_semantic_search_missing_db_returns_error(tmp_path: Path) -> None:
    absent_db = tmp_path / "missing.db"

    with patch("parsidion_mcp.tools.search.vault_common") as mock_vc:
        mock_vc.get_embeddings_db_path.return_value = absent_db
        result = vault_search(query="anything")

    assert result.startswith("ERROR: embeddings DB not found")


def test_semantic_search_respects_top_k_and_min_score(tmp_path: Path) -> None:
    db = tmp_path / "embeddings.db"
    db.touch()

    with (
        patch("parsidion_mcp.tools.search.vault_common") as mock_vc,
        patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs,
    ):
        mock_vc.get_embeddings_db_path.return_value = db
        mock_vs.search.return_value = []
        vault_search(query="q", top_k=5, min_score=0.6)

    mock_vs.search.assert_called_once_with("q", top=5, min_score=0.6)


# ---------------------------------------------------------------------------
# Metadata mode
# ---------------------------------------------------------------------------

def test_metadata_search_returns_json() -> None:
    with patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs:
        mock_vs.query.return_value = [_FAKE_NOTE]
        result = vault_search(tag="python", folder="Patterns")

    parsed = json.loads(result)
    assert parsed[0]["folder"] == "Patterns"
    mock_vs.query.assert_called_once_with(
        tag="python",
        folder="Patterns",
        note_type=None,
        project=None,
        recent_days=None,
        limit=10,
    )


def test_metadata_search_empty_returns_empty_json() -> None:
    with patch("parsidion_mcp.tools.search._vault_search_module") as mock_vs:
        mock_vs.query.return_value = []
        result = vault_search(folder="Nonexistent")

    assert json.loads(result) == []
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
uv run pytest tests/test_search.py -v
```

Expected: `ImportError` — `parsidion_mcp.tools.search` doesn't exist.

- [ ] **Step 3: Implement `search.py`**

`parsidion-mcp/src/parsidion_mcp/tools/search.py`:
```python
"""vault_search MCP tool — semantic and metadata modes."""

import json

import vault_common

# vault_search.py mutates sys.path at import time (adds its own directory).
# This is an intentional design of the standalone script; the side effect is
# benign here — it ensures vault_common remains resolvable at runtime.
import vault_search as _vault_search_module


def vault_search(
    query: str | None = None,
    tag: str | None = None,
    folder: str | None = None,
    note_type: str | None = None,
    project: str | None = None,
    recent_days: int | None = None,
    top_k: int = 10,
    min_score: float = 0.35,
) -> str:
    """Search vault notes using semantic or metadata mode.

    Semantic mode is used when *query* is provided; metadata mode otherwise.

    Args:
        query: Natural language query (enables semantic search).
        tag: Filter by exact tag token.
        folder: Filter by folder name.
        note_type: Filter by note type.
        project: Filter by project name.
        recent_days: Only notes modified within this many days.
        top_k: Maximum number of results.
        min_score: Minimum cosine similarity threshold (semantic mode only).

    Returns:
        JSON array of note objects, or an ERROR string on failure.
    """
    if query is not None:
        db_path = vault_common.get_embeddings_db_path()
        if not db_path.exists():
            return "ERROR: embeddings DB not found — run rebuild_index first"
        results = _vault_search_module.search(query, top=top_k, min_score=min_score)
    else:
        results = _vault_search_module.query(
            tag=tag,
            folder=folder,
            note_type=note_type,
            project=project,
            recent_days=recent_days,
            limit=top_k,
        )

    return json.dumps(results, default=str, indent=2)
```

- [ ] **Step 4: Run tests — verify they all pass**

```bash
uv run pytest tests/test_search.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parsidion-mcp/src/parsidion_mcp/tools/search.py parsidion-mcp/tests/test_search.py
git commit -m "feat(mcp): add vault_search tool"
```

---

### Task 4: vault_context (TDD)

**Files:**
- Create: `parsidion-mcp/src/parsidion_mcp/tools/context.py`
- Create: `parsidion-mcp/tests/test_context.py`

- [ ] **Step 1: Write failing tests**

`parsidion-mcp/tests/test_context.py`:
```python
"""Tests for vault_context tool."""

from pathlib import Path
from unittest.mock import patch, MagicMock, call

from parsidion_mcp.tools.context import vault_context, _build_compact_index


# ---------------------------------------------------------------------------
# _build_compact_index
# ---------------------------------------------------------------------------

def test_build_compact_index_formats_notes(tmp_path: Path) -> None:
    note = tmp_path / "Patterns" / "test.md"
    note.parent.mkdir()
    note.write_text(
        "---\ntags: [python, pattern]\n---\n# Test Note\n", encoding="utf-8"
    )

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.parse_frontmatter.return_value = {"tags": ["python", "pattern"]}
        mock_vc.extract_title.return_value = "Test Note"

        result = _build_compact_index([note])

    assert "[[test]]" in result
    assert "Test Note" in result
    assert "`python`" in result
    assert "Patterns" in result
    assert "**Available vault notes**" in result


def test_build_compact_index_empty_returns_message() -> None:
    with patch("parsidion_mcp.tools.context.vault_common"):
        result = _build_compact_index([])

    assert "No vault notes" in result


def test_build_compact_index_truncates_at_max_chars(tmp_path: Path) -> None:
    notes = []
    for i in range(20):
        n = tmp_path / f"note-{i}.md"
        n.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")
        notes.append(n)

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "A" * 80  # long title

        result = _build_compact_index(notes, max_chars=200)

    assert "more notes" in result


# ---------------------------------------------------------------------------
# vault_context
# ---------------------------------------------------------------------------

def test_vault_context_with_project(tmp_path: Path) -> None:
    note = tmp_path / "proj.md"
    note.write_text("---\ntags: []\n---\n# Proj\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = [note]
        mock_vc.find_recent_notes.return_value = []
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "Proj"

        result = vault_context(project="myproject", recent_days=3)

    mock_vc.find_notes_by_project.assert_called_once_with("myproject")
    mock_vc.find_recent_notes.assert_called_once_with(3)
    assert "[[proj]]" in result


def test_vault_context_deduplicates_notes(tmp_path: Path) -> None:
    note = tmp_path / "dup.md"
    note.write_text("---\ntags: []\n---\n# Dup\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = [note]
        mock_vc.find_recent_notes.return_value = [note]  # same note
        mock_vc.parse_frontmatter.return_value = {"tags": []}
        mock_vc.extract_title.return_value = "Dup"

        result = vault_context(project="x")

    # Should appear only once
    assert result.count("[[dup]]") == 1


def test_vault_context_verbose_calls_build_context_block(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text("---\ntags: []\n---\n# Note\n", encoding="utf-8")

    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.VAULT_ROOT = tmp_path
        mock_vc.find_notes_by_project.return_value = []
        mock_vc.find_recent_notes.return_value = [note]
        mock_vc.build_context_block.return_value = "VERBOSE CONTEXT"

        result = vault_context(verbose=True)

    assert result == "VERBOSE CONTEXT"
    mock_vc.build_context_block.assert_called_once()


def test_vault_context_no_notes_returns_message() -> None:
    with patch("parsidion_mcp.tools.context.vault_common") as mock_vc:
        mock_vc.find_recent_notes.return_value = []
        result = vault_context()

    assert "No relevant" in result
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
uv run pytest tests/test_context.py -v
```

Expected: `ImportError` — `parsidion_mcp.tools.context` doesn't exist.

- [ ] **Step 3: Implement `context.py`**

`parsidion-mcp/src/parsidion_mcp/tools/context.py`:
```python
"""vault_context MCP tool — session-start-style vault context."""

from pathlib import Path

import vault_common


def _build_compact_index(notes: list[Path], max_chars: int = 4000) -> str:
    """Build a compact one-line-per-note index.

    Format matches session_start_hook.build_compact_index():
      - [[stem]] Title (folder) — `tag1` `tag2`

    Args:
        notes: Ordered list of note Paths to include.
        max_chars: Maximum total characters before truncating.

    Returns:
        Formatted compact index string, or a "no notes" message if empty.
    """
    if not notes:
        return "No vault notes available."

    vault_root = vault_common.VAULT_ROOT
    lines: list[str] = []
    total = 0

    for path in notes:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = vault_common.parse_frontmatter(content)
        title = vault_common.extract_title(content, path.stem)
        tags = fm.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        tag_str = " ".join(f"`{t}`" for t in tags) if tags else ""
        folder = path.parent.name if path.parent != vault_root else "root"
        entry = f"- [[{path.stem}]] {title} ({folder})"
        if tag_str:
            entry += f" — {tag_str}"
        total += len(entry) + 1
        if total > max_chars:
            remaining = len(notes) - len(lines)
            lines.append(
                f"- ... ({remaining} more notes, use parsidion skill to browse)"
            )
            break
        lines.append(entry)

    if not lines:
        return "No vault notes available."

    header = (
        "**Available vault notes** (compact index — "
        "use `parsidion` skill to load full content):\n"
    )
    return header + "\n".join(lines)


def vault_context(
    project: str | None = None,
    recent_days: int = 3,
    verbose: bool = False,
) -> str:
    """Return vault context for injection into a system prompt.

    Mirrors the session_start_hook context format. Compact one-line index by
    default; full summaries when *verbose* is True.

    Args:
        project: Project name to prioritize notes for.
        recent_days: Include notes modified within this many days.
        verbose: When True, return full note summaries instead of compact index.

    Returns:
        Context string ready for system prompt injection.
    """
    notes: list[Path] = []
    seen: set[Path] = set()

    if project:
        for p in vault_common.find_notes_by_project(project):
            if p not in seen:
                notes.append(p)
                seen.add(p)

    for p in vault_common.find_recent_notes(recent_days):
        if p not in seen:
            notes.append(p)
            seen.add(p)

    if not notes:
        return "No relevant vault notes found."

    if verbose:
        return vault_common.build_context_block(notes)

    return _build_compact_index(notes)
```

- [ ] **Step 4: Run tests — verify they all pass**

```bash
uv run pytest tests/test_context.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parsidion-mcp/src/parsidion_mcp/tools/context.py parsidion-mcp/tests/test_context.py
git commit -m "feat(mcp): add vault_context tool"
```

---

## Chunk 3: ops.py + server.py + integration + install

### Task 5: rebuild_index and vault_doctor (TDD)

**Files:**
- Create: `parsidion-mcp/src/parsidion_mcp/tools/ops.py`
- Create: `parsidion-mcp/tests/test_ops.py`

- [ ] **Step 1: Write failing tests**

`parsidion-mcp/tests/test_ops.py`:
```python
"""Tests for rebuild_index and vault_doctor tools."""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

from parsidion_mcp.tools.ops import rebuild_index, vault_doctor


def _make_proc(returncode: int = 0, stdout: str = "ok", stderr: str = "") -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ---------------------------------------------------------------------------
# rebuild_index
# ---------------------------------------------------------------------------

def test_rebuild_index_success() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="Index rebuilt.")
        result = rebuild_index()

    assert result == "Index rebuilt."
    cmd = mock_run.call_args[0][0]
    assert "update_index.py" in cmd[-1]
    assert cmd[:3] == ["uv", "run", "--no-project"]


def test_rebuild_index_nonzero_exit_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="something failed")
        result = rebuild_index()

    assert result.startswith("ERROR:")
    assert "something failed" in result


def test_rebuild_index_timeout_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=30)
        result = rebuild_index()

    assert "timed out" in result


def test_rebuild_index_timeout_is_30s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        rebuild_index()

    assert mock_run.call_args[1]["timeout"] == 30


# ---------------------------------------------------------------------------
# vault_doctor
# ---------------------------------------------------------------------------

def test_vault_doctor_scan_only_omits_fix_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="2 issues found.")
        result = vault_doctor(fix=False)

    cmd = mock_run.call_args[0][0]
    assert "--fix" not in cmd
    assert result == "2 issues found."


def test_vault_doctor_fix_true_includes_fix_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(stdout="Fixed 2 notes.")
        vault_doctor(fix=True)

    cmd = mock_run.call_args[0][0]
    assert "--fix" in cmd


def test_vault_doctor_errors_only_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(errors_only=True)

    cmd = mock_run.call_args[0][0]
    assert "--errors-only" in cmd


def test_vault_doctor_limit_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(limit=5)

    cmd = mock_run.call_args[0][0]
    assert "--limit" in cmd
    assert "5" in cmd


def test_vault_doctor_limit_none_omits_flag() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor(limit=None)

    cmd = mock_run.call_args[0][0]
    assert "--limit" not in cmd


def test_vault_doctor_nonzero_exit_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc(returncode=1, stderr="crashed")
        result = vault_doctor()

    assert result.startswith("ERROR:")


def test_vault_doctor_timeout_returns_error() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=120)
        result = vault_doctor()

    assert "timed out" in result


def test_vault_doctor_timeout_is_120s() -> None:
    with patch("parsidion_mcp.tools.ops.subprocess.run") as mock_run:
        mock_run.return_value = _make_proc()
        vault_doctor()

    assert mock_run.call_args[1]["timeout"] == 120
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
uv run pytest tests/test_ops.py -v
```

Expected: `ImportError` — `parsidion_mcp.tools.ops` doesn't exist.

- [ ] **Step 3: Implement `ops.py`**

`parsidion-mcp/src/parsidion_mcp/tools/ops.py`:
```python
"""rebuild_index and vault_doctor MCP tools."""

import subprocess
from pathlib import Path

import vault_common

# TEMPLATES_DIR is always <skill_root>/templates/.
# Scripts are one level up: <skill_root>/scripts/.
# This invariant holds because the installer only patches VAULT_ROOT and
# TEMPLATES_DIR always points into ~/.claude/skills/parsidion/.
SCRIPTS_DIR: Path = vault_common.TEMPLATES_DIR.parent / "scripts"


def rebuild_index() -> str:
    """Rebuild the vault index (CLAUDE.md, MANIFEST.md files, note_index table).

    Returns:
        Script output on success, or an ERROR string on failure.
    """
    script = SCRIPTS_DIR / "update_index.py"
    try:
        result = subprocess.run(
            ["uv", "run", "--no-project", str(script)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"ERROR: {output}"
        return output or "Index rebuilt successfully."
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"
    except (FileNotFoundError, OSError) as exc:
        return f"ERROR: {exc}"


def vault_doctor(
    fix: bool = False,
    errors_only: bool = False,
    limit: int | None = None,
) -> str:
    """Scan vault notes for structural issues; optionally repair them.

    Args:
        fix: When True, repair repairable issues via Claude haiku.
             When False, scan and report only (--fix flag is omitted).
        errors_only: When True, skip warnings and report errors only.
        limit: Maximum number of notes to repair (only relevant when fix=True).

    Returns:
        Scan/repair report, or an ERROR string on failure.
    """
    script = SCRIPTS_DIR / "vault_doctor.py"
    args: list[str] = ["uv", "run", "--no-project", str(script)]
    if fix:
        args.append("--fix")
    if errors_only:
        args.append("--errors-only")
    if limit is not None:
        args.extend(["--limit", str(limit)])

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return f"ERROR: {output}"
        return output or "Doctor scan complete."
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 120s"
    except (FileNotFoundError, OSError) as exc:
        return f"ERROR: {exc}"
```

- [ ] **Step 4: Run tests — verify they all pass**

```bash
uv run pytest tests/test_ops.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add parsidion-mcp/src/parsidion_mcp/tools/ops.py parsidion-mcp/tests/test_ops.py
git commit -m "feat(mcp): add rebuild_index and vault_doctor tools"
```

---

### Task 6: Server wiring + entry point

**Files:**
- Create: `parsidion-mcp/src/parsidion_mcp/server.py`
- Create: `parsidion-mcp/tests/test_server.py`

- [ ] **Step 1: Write smoke test**

`parsidion-mcp/tests/test_server.py`:
```python
"""Smoke tests for server.py wiring."""

from parsidion_mcp.server import mcp


def test_mcp_instance_exists() -> None:
    assert mcp is not None
    assert mcp.name == "parsidion-mcp"


def test_all_tool_modules_importable() -> None:
    """Verify all tool functions are importable and callable.

    Avoids FastMCP private internals (_tool_manager) which may change
    between versions. Correct registration is implicitly verified: if
    server.py imports cleanly and mcp.tool()(fn) raised no error at
    module load time, all tools are registered.
    """
    from parsidion_mcp.tools.context import vault_context
    from parsidion_mcp.tools.notes import vault_read, vault_write
    from parsidion_mcp.tools.ops import rebuild_index, vault_doctor
    from parsidion_mcp.tools.search import vault_search

    for fn in [vault_search, vault_read, vault_write, vault_context, rebuild_index, vault_doctor]:
        assert callable(fn)
```

- [ ] **Step 2: Run test — verify it fails**

```bash
uv run pytest tests/test_server.py -v
```

Expected: `ImportError` — `parsidion_mcp.server` doesn't exist.

- [ ] **Step 3: Implement `server.py`**

`parsidion-mcp/src/parsidion_mcp/server.py`:
```python
"""parsidion-mcp: FastMCP server exposing Parsidion vault to Claude Desktop."""

from fastmcp import FastMCP

from parsidion_mcp.tools.context import vault_context
from parsidion_mcp.tools.notes import vault_read, vault_write
from parsidion_mcp.tools.ops import rebuild_index, vault_doctor
from parsidion_mcp.tools.search import vault_search

mcp = FastMCP("parsidion-mcp")

mcp.tool()(vault_search)
mcp.tool()(vault_read)
mcp.tool()(vault_write)
mcp.tool()(vault_context)
mcp.tool()(rebuild_index)
mcp.tool()(vault_doctor)


def main() -> None:
    """Entry point for the ``parsidion-mcp`` command."""
    mcp.run()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
uv run pytest tests/test_server.py -v
```

Expected: both tests PASS.

> **Note:** If fastmcp's internal API for `_tool_manager.list_tools()` differs in the installed version, adapt the test to use whatever method fastmcp exposes for listing registered tools (e.g. `mcp.list_tools()` or introspecting `mcp._tools`). The goal is confirming all 6 tools are registered.

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -v
```

Expected: all tests PASS.

- [ ] **Step 6: Run checkall**

```bash
make checkall
```

Expected: fmt, lint, typecheck, and tests all pass with no errors.

- [ ] **Step 7: Commit**

```bash
git add parsidion-mcp/src/parsidion_mcp/server.py parsidion-mcp/tests/test_server.py
git commit -m "feat(mcp): wire FastMCP server with all six tools"
```

---

### Task 7: Integration smoke test + install verification

**Files:**
- Create: `parsidion-mcp/tests/test_integration.py`

- [ ] **Step 1: Write integration smoke test**

`parsidion-mcp/tests/test_integration.py`:
```python
"""Integration smoke test — skipped when vault is absent."""

import pytest
import vault_common

VAULT_PRESENT = vault_common.VAULT_ROOT.exists()


@pytest.mark.skipif(not VAULT_PRESENT, reason="vault not present")
def test_vault_read_real_note() -> None:
    """Read the first available vault note without errors."""
    from parsidion_mcp.tools.notes import vault_read

    notes = list(vault_common.VAULT_ROOT.rglob("*.md"))
    notes = [n for n in notes if ".obsidian" not in n.parts]

    if not notes:
        pytest.skip("no notes in vault")

    rel = notes[0].relative_to(vault_common.VAULT_ROOT)
    result = vault_read(str(rel))
    assert not result.startswith("ERROR:"), f"vault_read failed: {result}"


@pytest.mark.skipif(not VAULT_PRESENT, reason="vault not present")
def test_vault_context_returns_string() -> None:
    """vault_context returns a non-empty string."""
    from parsidion_mcp.tools.context import vault_context

    result = vault_context(recent_days=30)
    assert isinstance(result, str)
    assert len(result) > 0
```

- [ ] **Step 2: Run integration tests**

```bash
uv run pytest tests/test_integration.py -v
```

Expected: tests run (if vault present) or skip (if absent). No failures.

- [ ] **Step 3: Install as a tool**

```bash
cd parsidion-mcp
uv tool install --editable .
```

Expected: no errors. Verify:
```bash
which parsidion-mcp
parsidion-mcp --help
```

Expected: binary resolves (typically `~/.local/bin/parsidion-mcp`). `--help` prints FastMCP usage.

- [ ] **Step 4: Configure Claude Desktop**

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "parsidion": {
      "command": "/Users/probello/.local/bin/parsidion-mcp"
    }
  }
}
```

Replace the path with the output of `which parsidion-mcp`.

- [ ] **Step 5: Final commit**

```bash
git add parsidion-mcp/tests/test_integration.py
git commit -m "feat(mcp): add integration smoke tests and install instructions"
```

---

## Summary

| Task | Files created | Tests |
|---|---|---|
| 1: Scaffold | `pyproject.toml`, `Makefile`, `__init__.py` files | — |
| 2: vault_read/write | `tools/notes.py` | `test_notes.py` (9 tests) |
| 3: vault_search | `tools/search.py` | `test_search.py` (5 tests) |
| 4: vault_context | `tools/context.py` | `test_context.py` (6 tests) |
| 5: rebuild_index/doctor | `tools/ops.py` | `test_ops.py` (10 tests) |
| 6: Server wiring | `server.py` | `test_server.py` (2 tests) |
| 7: Integration + install | — | `test_integration.py` (2 tests, skippable) |
