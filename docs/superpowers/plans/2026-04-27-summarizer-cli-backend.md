# Summarizer CLI Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove `claude-agent-sdk` from `summarize_sessions.py` and route summarizer model calls through the existing CLI prompt backend (`claude -p` or `codex exec`).

**Architecture:** Keep `summarize_sessions.py` as the queue/concurrency/orchestration module. Add a small async wrapper around `ai_backend.run_ai_prompt()` using `anyio.to_thread.run_sync`, then replace the two Claude Agent SDK query sites with backend-neutral CLI calls.

**Tech Stack:** Python 3.13, anyio, existing `ai_backend.py`, pytest monkeypatching, ruff, pyright.

---

## File Structure

- Modify `skills/parsidion/scripts/summarize_sessions.py`
  - Remove `claude-agent-sdk` PEP 723 dependency and imports.
  - Import `ai_backend`.
  - Add `_run_summarizer_prompt()` async helper.
  - Migrate `_summarize_chunk()` to small-tier CLI backend.
  - Migrate `summarize_one()` to large-tier CLI backend.
  - Preserve queue/write/index behavior.
- Create `tests/test_summarize_sessions.py`
  - Focused tests for chunk prompt backend use, fallback behavior, final note backend use, write-gate preservation, and no SDK dependency.
- Modify `skills/parsidion/scripts/vault_config.py`
  - Allow `summarizer.model` and `summarizer.cluster_model` to be `null`.
- Modify docs/config examples:
  - `README.md`
  - `skills/parsidion/SKILL.md`
  - `CHANGELOG.md`
  - `skills/parsidion/templates/config.yaml`

---

### Task 1: Summarizer backend helper and SDK removal

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`
- Modify: `skills/parsidion/scripts/vault_config.py`
- Create: `tests/test_summarize_sessions.py`

- [ ] **Step 1: Write failing dependency/import tests**

Create `tests/test_summarize_sessions.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "skills" / "parsidion" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import summarize_sessions  # noqa: E402


def test_summarizer_pep723_no_longer_depends_on_claude_agent_sdk() -> None:
    source = Path(summarize_sessions.__file__).read_text(encoding="utf-8")

    assert "claude-agent-sdk" not in source
    assert "claude_agent_sdk" not in source


def test_summarizer_imports_ai_backend() -> None:
    assert hasattr(summarize_sessions, "ai_backend")
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd /Users/probello/Repos/parsidion/.worktrees/summarizer-cli-backend
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: fails because `summarize_sessions.py` still contains `claude-agent-sdk` and `claude_agent_sdk` imports and does not import `ai_backend`.

- [ ] **Step 3: Remove SDK dependency/import and add backend helper**

In `skills/parsidion/scripts/summarize_sessions.py`:

Change PEP 723 dependencies from:

```python
# dependencies = ["claude-agent-sdk>=0.0.10,<1.0", "anyio>=4.0.0,<5.0"]
```

to:

```python
# dependencies = ["anyio>=4.0.0,<5.0"]
```

Remove:

```python
from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query  # type: ignore[import-untyped]
```

Add near other local imports:

```python
import ai_backend
```

Add helper near the model constants:

```python
async def _run_summarizer_prompt(
    prompt: str,
    *,
    model: str | None,
    model_tier: ai_backend.ModelTier,
    purpose: str,
    timeout: int | float | None,
    vault: Path,
) -> str | None:
    """Run a summarizer prompt through the configured CLI AI backend."""

    def _run() -> str | None:
        return ai_backend.run_ai_prompt(
            prompt,
            model=model,
            model_tier=model_tier,
            timeout=timeout,
            purpose=purpose,
            vault=vault,
        )

    return await anyio.to_thread.run_sync(_run)
```

- [ ] **Step 4: Update config schema for nullable summarizer models**

In `skills/parsidion/scripts/vault_config.py`, update `_CONFIG_SCHEMA["summarizer"]`:

```python
        "model": (str, type(None)),
        "cluster_model": (str, type(None)),
```

Leave other summarizer keys unchanged.

- [ ] **Step 5: Run initial tests to verify dependency/import GREEN**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: the two new tests pass, while later functionality is not migrated yet.

- [ ] **Step 6: Commit Task 1**

Run:

```bash
uv run ruff format skills/parsidion/scripts/summarize_sessions.py skills/parsidion/scripts/vault_config.py tests/test_summarize_sessions.py
uv run ruff check skills/parsidion/scripts/summarize_sessions.py skills/parsidion/scripts/vault_config.py tests/test_summarize_sessions.py
uv run pyright skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git add skills/parsidion/scripts/summarize_sessions.py skills/parsidion/scripts/vault_config.py tests/test_summarize_sessions.py
git commit -m "feat: prepare summarizer cli backend"
```

---

### Task 2: Migrate chunk summarization to CLI backend

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`
- Modify: `tests/test_summarize_sessions.py`

- [ ] **Step 1: Write failing chunk backend test**

Append to `tests/test_summarize_sessions.py`:

```python
@pytest.mark.anyio
async def test_summarize_chunk_uses_small_tier_backend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: Any) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "chunk summary"

    monkeypatch.setattr(summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt)

    result = await summarize_sessions._summarize_chunk(
        "transcript chunk",
        1,
        2,
        model=None,
        vault=tmp_path,
    )

    assert result == "chunk summary"
    assert calls
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "small"
    assert calls[0]["purpose"] == "summarizer-chunk"
    assert calls[0]["vault"] == tmp_path


@pytest.mark.anyio
async def test_summarize_chunk_falls_back_to_raw_chunk_on_backend_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_run_summarizer_prompt(prompt: str, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt)

    result = await summarize_sessions._summarize_chunk(
        "x" * 700,
        1,
        1,
        model=None,
        vault=tmp_path,
    )

    assert result == "x" * 500
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: fails because `_summarize_chunk()` still has old signature/SDK code.

- [ ] **Step 3: Migrate `_summarize_chunk()`**

Change signature from:

```python
async def _summarize_chunk(
    chunk_text: str,
    chunk_num: int,
    total_chunks: int,
    model: str,
    extra: dict[str, str | None],
) -> str:
```

to:

```python
async def _summarize_chunk(
    chunk_text: str,
    chunk_num: int,
    total_chunks: int,
    model: str | None,
    vault: Path,
) -> str:
```

Replace SDK query block with:

```python
    result_text = await _run_summarizer_prompt(
        prompt,
        model=model,
        model_tier="small",
        purpose="summarizer-chunk",
        timeout=vault_common.get_config("summarizer", "ai_timeout", None),
        vault=vault,
    )
```

Then keep existing fallback:

```python
    if result_text:
        return result_text
    return chunk_text[:500]
```

- [ ] **Step 4: Update `preprocess_transcript_hierarchical()` signature/call**

Change its parameters:

```python
    cluster_model: str | None,
    vault: Path,
```

Remove `extra` parameter.

Change chunk call:

```python
summary = await _summarize_chunk(chunk, i + 1, total, cluster_model, vault)
```

Update callers to pass `vault_path` instead of `extra`.

- [ ] **Step 5: Run chunk tests GREEN**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: chunk tests pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
uv run ruff format skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
uv run ruff check skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
uv run pyright skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git add skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git commit -m "feat: route chunk summarization through cli backend"
```

---

### Task 3: Migrate final note generation to CLI backend

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`
- Modify: `tests/test_summarize_sessions.py`

- [ ] **Step 1: Write failing final-note backend test**

Append to `tests/test_summarize_sessions.py`:

```python
@pytest.mark.anyio
async def test_summarize_one_uses_large_tier_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"assistant","message":{"content":"fixed bug"}}\n', encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()

    calls: list[dict[str, Any]] = []

    async def fake_preprocess(*args: Any, **kwargs: Any) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: Any) -> str:
        calls.append({"prompt": prompt, **kwargs})
        return "---\ntitle: Test Note\ntags: [debugging]\ntype: debugging\n---\n# Test Note\n\nUseful note."

    monkeypatch.setattr(summarize_sessions, "preprocess_transcript_hierarchical", fake_preprocess)
    monkeypatch.setattr(summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt)
    monkeypatch.setattr(summarize_sessions, "read_existing_tags", lambda vault: ["debugging"])
    monkeypatch.setattr(summarize_sessions, "read_project_names", lambda vault_notes=None: [])
    monkeypatch.setattr(summarize_sessions, "_find_dedup_candidates", lambda *args, **kwargs: [])

    entry = {
        "session_id": "sess-1",
        "transcript_path": str(transcript),
        "project": "parsidion",
        "categories": ["error_fix"],
    }

    result_entry, written = await summarize_sessions.summarize_one(
        entry,
        model=None,
        dry_run=True,
        semaphore=summarize_sessions.anyio.Semaphore(1),
        existing_tags=["debugging"],
        project_names=[],
        vault=vault,
        persist=False,
        tail_lines=200,
        max_cleaned_chars=12000,
        cluster_model=None,
        vault_notes=[],
    )

    assert result_entry == entry
    assert written is not None
    assert calls
    assert calls[0]["model"] is None
    assert calls[0]["model_tier"] == "large"
    assert calls[0]["purpose"] == "summarizer-note"
    assert calls[0]["vault"] == vault
```

- [ ] **Step 2: Write failing write-gate skip preservation test**

Append:

```python
@pytest.mark.anyio
async def test_summarize_one_preserves_skip_write_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"assistant","message":{"content":"routine edit"}}\n', encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()

    async def fake_preprocess(*args: Any, **kwargs: Any) -> str:
        return "cleaned transcript"

    async def fake_run_summarizer_prompt(prompt: str, **kwargs: Any) -> str:
        return '{"decision":"skip","reason":"routine"}'

    monkeypatch.setattr(summarize_sessions, "preprocess_transcript_hierarchical", fake_preprocess)
    monkeypatch.setattr(summarize_sessions, "_run_summarizer_prompt", fake_run_summarizer_prompt)
    monkeypatch.setattr(summarize_sessions, "_find_dedup_candidates", lambda *args, **kwargs: [])

    entry = {
        "session_id": "sess-2",
        "transcript_path": str(transcript),
        "project": "parsidion",
        "categories": ["pattern"],
    }

    _entry, written = await summarize_sessions.summarize_one(
        entry,
        model=None,
        dry_run=False,
        semaphore=summarize_sessions.anyio.Semaphore(1),
        existing_tags=[],
        project_names=[],
        vault=vault,
        persist=False,
        tail_lines=200,
        max_cleaned_chars=12000,
        cluster_model=None,
        vault_notes=[],
    )

    assert written is None
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: fails because `summarize_one()` still uses SDK query and `preprocess_transcript_hierarchical()` signature may not match.

- [ ] **Step 4: Migrate `summarize_one()` SDK query**

In `summarize_one()` remove `extra` usage and replace SDK query block with:

```python
        result_text = await _run_summarizer_prompt(
            prompt,
            model=model,
            model_tier="large",
            purpose="summarizer-note",
            timeout=vault_common.get_config("summarizer", "ai_timeout", None),
            vault=vault,
        ) or ""
```

Keep existing exception print behavior by wrapping the call if desired:

```python
        try:
            result_text = await _run_summarizer_prompt(... ) or ""
        except Exception as e:  # noqa: BLE001
            print(f"  Error querying AI backend for {transcript_path_str}: {e}", file=sys.stderr)
            return entry, None
```

Remove references to `ClaudeAgentOptions`, `ResultMessage`, and `query`.

- [ ] **Step 5: Update `run_all()` and model types**

Change `model` and `cluster_model` type hints to `str | None` where needed:

```python
async def run_all(
    entries: list[dict[str, object]],
    model: str | None,
    ...,
    cluster_model: str | None = None,
) -> ...
```

Update `summarize_one()` signature similarly:

```python
model: str | None
cluster_model: str | None = None
```

Remove construction/passing of SDK `extra_args`; keep `persist` parameter accepted but unused. To avoid unused-argument warnings, add:

```python
    del persist
```

inside `summarize_one()` or pass it through only for future compatibility.

- [ ] **Step 6: Run summarizer tests GREEN**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py -q
```

Expected: all summarizer tests pass.

- [ ] **Step 7: Commit Task 3**

Run:

```bash
uv run ruff format skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
uv run ruff check skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
uv run pyright skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git add skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git commit -m "feat: route final summarization through cli backend"
```

---

### Task 4: CLI/config model resolution and docs

**Files:**
- Modify: `skills/parsidion/scripts/summarize_sessions.py`
- Modify: `README.md`
- Modify: `skills/parsidion/SKILL.md`
- Modify: `CHANGELOG.md`
- Modify: `skills/parsidion/templates/config.yaml`
- Modify: `tests/test_summarize_sessions.py`

- [ ] **Step 1: Write failing model-resolution test**

Append to `tests/test_summarize_sessions.py`:

```python
def test_main_uses_backend_defaults_when_summarizer_models_are_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sessions = tmp_path / "sessions.jsonl"
    sessions.write_text("", encoding="utf-8")
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "config.yaml").write_text(
        "ai:\n  backend: codex-cli\n"
        "summarizer:\n  model: null\n  cluster_model: null\n",
        encoding="utf-8",
    )
    observed: dict[str, Any] = {}

    def fake_read_pending(path: Path) -> list[dict[str, object]]:
        return [{"session_id": "s", "transcript_path": str(tmp_path / "t.jsonl"), "project": "p", "categories": ["research"]}]

    async def fake_run_all(entries: list[dict[str, object]], model: str | None, dry_run: bool, persist: bool, vault_path: Path, max_parallel: int, tail_lines: int, max_cleaned_chars: int, cluster_model: str | None) -> list[tuple[dict[str, object], Path | str | None]]:
        observed["model"] = model
        observed["cluster_model"] = cluster_model
        return [(entries[0], None)]

    monkeypatch.setattr(summarize_sessions, "read_pending", fake_read_pending)
    monkeypatch.setattr(summarize_sessions, "run_all", fake_run_all)
    monkeypatch.setattr(sys, "argv", ["summarize_sessions.py", "--sessions", str(sessions), "--vault", str(vault), "--dry-run"])

    summarize_sessions.main()

    assert observed["model"] is None
    assert observed["cluster_model"] is None
```

- [ ] **Step 2: Run test to verify RED**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py::test_main_uses_backend_defaults_when_summarizer_models_are_null -q
```

Expected: may fail because current `model` variable is typed/defaulted to Claude model or config schema rejects null.

- [ ] **Step 3: Update main model resolution**

In `main()` change:

```python
model: str = (
    args.model
    if args.model is not None
    else vault_common.get_config("summarizer", "model", _DEFAULT_MODEL)
)
```

to:

```python
configured_model = vault_common.get_config("summarizer", "model", None)
model: str | None = args.model if args.model is not None else configured_model
```

Change cluster model resolution to:

```python
cluster_model: str | None = vault_common.get_config(
    "summarizer",
    "cluster_model",
    None,
)
```

Update status print:

```python
model_label = model or "backend large default"
print(f"Processing {len(entries)} session(s) with model {model_label}...")
```

Do not use `_DEFAULT_MODEL` or `_DEFAULT_CLUSTER_MODEL` as implicit runtime values. Remove constants if unused, or keep only for documentation if needed.

- [ ] **Step 4: Update docs/config template**

In `README.md`, `skills/parsidion/SKILL.md`, and `skills/parsidion/templates/config.yaml`, change summarizer examples:

```yaml
summarizer:
  model: null          # null = ai_models.<backend>.large
  cluster_model: null  # null = ai_models.<backend>.small
```

Replace text saying summarizer uses `claude-agent-sdk` with text saying it uses the configured prompt AI backend. Mention:

- Claude backend uses `claude -p`.
- Codex backend uses `codex exec`.
- No Claude Agent SDK or Codex SDK is required for this path.
- `anyio` remains for concurrency.

Add `CHANGELOG.md` entry:

```markdown
- **CLI-backed session summarizer** — `summarize_sessions.py` no longer depends on `claude-agent-sdk`; it now uses the configured prompt AI backend, enabling Codex summarization through `codex exec` with backend-aware small/large model defaults.
```

- [ ] **Step 5: Run focused docs/config tests**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py tests/test_vault_common.py::TestParseConfigYaml -q
rg 'claude-agent-sdk|codex exec|backend large default|cluster_model: null|model: null' README.md skills/parsidion/SKILL.md CHANGELOG.md skills/parsidion/templates/config.yaml skills/parsidion/scripts/summarize_sessions.py
```

Expected:

- Tests pass.
- `claude-agent-sdk` should not appear in `summarize_sessions.py`; docs may mention it only historically if clearly saying it was removed.

- [ ] **Step 6: Commit Task 4**

Run:

```bash
uv run ruff format skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
uv run ruff check skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py
git add skills/parsidion/scripts/summarize_sessions.py tests/test_summarize_sessions.py README.md skills/parsidion/SKILL.md CHANGELOG.md skills/parsidion/templates/config.yaml skills/parsidion/scripts/vault_config.py
git commit -m "docs: document cli-backed summarizer"
```

---

### Task 5: Final verification and cleanup

**Files:**
- Modify only files needed to fix verification failures.

- [ ] **Step 1: Confirm SDK removal**

Run:

```bash
rg 'claude_agent_sdk|claude-agent-sdk|ClaudeAgentOptions|ResultMessage|query\(' skills/parsidion/scripts/summarize_sessions.py
```

Expected: no matches.

- [ ] **Step 2: Confirm backend usage**

Run:

```bash
rg '_run_summarizer_prompt|run_ai_prompt|model_tier="small"|model_tier="large"' skills/parsidion/scripts/summarize_sessions.py
```

Expected: matches for helper, chunk small tier, and final note large tier.

- [ ] **Step 3: Run targeted tests**

Run:

```bash
uv run pytest tests/test_summarize_sessions.py tests/test_ai_backend.py tests/test_vault_doctor.py -q
```

Expected: all pass.

- [ ] **Step 4: Run full verification**

Run:

```bash
make checkall
```

Expected: ruff format/check, pyright, and pytest all pass.

- [ ] **Step 5: Optional local dry-run smoke**

Create a temporary vault/session JSONL and monkeypatching is already covered by tests. If running a real dry-run, use the configured Codex backend only if the user wants to spend tokens. Otherwise skip and report not run.

- [ ] **Step 6: Commit verification fixes if needed**

If verification required fixes:

```bash
git add <changed-files>
git commit -m "fix: finalize cli-backed summarizer"
```

If no fixes were needed, do not create an empty commit.

- [ ] **Step 7: Final status**

Run:

```bash
git status --short --branch
git log --oneline --max-count=8
```

Expected: clean worktree on `feature/summarizer-cli-backend`.
