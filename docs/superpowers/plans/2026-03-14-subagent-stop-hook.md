# SubagentStop Hook Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tap the `SubagentStop` lifecycle hook to capture subagent learnings and route them into the existing `pending_summaries.jsonl` queue for AI summarization.

**Architecture:** Move shared transcript-parsing and queue-append logic from `session_stop_hook.py` into `vault_common.py`. Create a new `subagent_stop_hook.py` that reads `agent_transcript_path` from the hook input and calls the shared functions. Register the hook with `async: true` so it never blocks running subagents.

**Tech Stack:** Python stdlib only, `vault_common.py` shared library, `~/.claude/settings.json` hook registration, `install.py` for installer sync.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `skills/parsidion/scripts/vault_common.py` | Modify | Add `_CATEGORIES`, `_CATEGORY_LABELS`, `parse_transcript_lines()`, `detect_categories()`, `append_to_pending()` |
| `skills/parsidion/scripts/session_stop_hook.py` | Modify | Remove moved functions, import from `vault_common` |
| `skills/parsidion/scripts/subagent_stop_hook.py` | **Create** | New `SubagentStop` hook script |
| `install.py` | Modify | Add `SubagentStop` to `_HOOK_SCRIPTS`; add `_HOOK_OPTIONS` for `async: true`; update `merge_hooks` |
| `skills/parsidion/templates/config.yaml` | Modify | Add `subagent_stop_hook` section |
| `~/.claude/settings.json` | Auto (via install.py) | Register `SubagentStop` hook |
| Installed scripts | Auto (via install.py) | Sync new/changed scripts to `~/.claude/skills/parsidion/scripts/` |

---

## Chunk 1: Refactor shared logic into vault_common

### Task 1: Add shared transcript functions to vault_common.py

**Files:**
- Modify: `skills/parsidion/scripts/vault_common.py`

- [ ] **Step 1: Add `_CATEGORIES`, `_CATEGORY_LABELS` constants and `parse_transcript_lines`, `detect_categories`, `append_to_pending` to `vault_common.py`**

Add the following block just before the `git_commit_vault` function (after the transcript helpers section). Also add the new names to `__all__`.

Add to `__all__`:
```python
    # Transcript analysis and queuing (shared by session_stop and subagent_stop hooks)
    "TRANSCRIPT_CATEGORIES",
    "TRANSCRIPT_CATEGORY_LABELS",
    "parse_transcript_lines",
    "detect_categories",
    "append_to_pending",
```

Add constants and functions after the `read_last_n_lines` function:

```python
# ---------------------------------------------------------------------------
# Transcript analysis helpers (shared by session_stop and subagent_stop hooks)
# ---------------------------------------------------------------------------

TRANSCRIPT_CATEGORIES: dict[str, list[str]] = {
    "error_fix": [
        "fixed",
        "the issue was",
        "root cause",
        "the error",
        "resolved by",
        "the fix",
        "bug was",
        "problem was",
        "workaround",
    ],
    "research": [
        "found that",
        "documentation says",
        "according to",
        "turns out",
        "discovered that",
        "learned that",
        "it appears",
        "the docs say",
        "the spec says",
    ],
    "pattern": [
        "pattern",
        "approach",
        "technique",
        "best practice",
        "convention",
        "idiom",
        "architecture",
        "design decision",
    ],
    "config_setup": [
        "configured",
        "installed",
        "set up",
        "added to",
        "created",
        "initialized",
        "migrated",
        "deployed",
    ],
}

TRANSCRIPT_CATEGORY_LABELS: dict[str, str] = {
    "error_fix": "Error Resolution",
    "research": "Research Findings",
    "pattern": "Pattern Discovery",
    "config_setup": "Config/Setup",
}


def parse_transcript_lines(lines: list[str]) -> list[str]:
    """Parse JSONL transcript lines and extract assistant message text.

    Args:
        lines: Raw JSONL lines from the transcript file.

    Returns:
        A list of text strings from assistant messages.
    """
    assistant_texts: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if entry.get("type") != "assistant":
            continue

        message = entry.get("message", entry)
        content = message.get("content")
        if content is None:
            continue

        text = extract_text_from_content(content)
        if text.strip():
            assistant_texts.append(text)

    return assistant_texts


def detect_categories(texts: list[str]) -> dict[str, list[str]]:
    """Scan assistant texts for learnable content using keyword heuristics.

    Args:
        texts: List of assistant message texts.

    Returns:
        Dict mapping category keys to lists of matching text excerpts
        (each truncated to 500 chars).
    """
    found: dict[str, list[str]] = {}

    for text in texts:
        text_lower = text.lower()
        for category, keywords in TRANSCRIPT_CATEGORIES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    if category not in found:
                        found[category] = []
                    excerpt = text[:500].strip()
                    if excerpt and excerpt not in found[category]:
                        found[category].append(excerpt)
                    break

    return found


def append_to_pending(
    transcript_path: Path,
    project: str,
    categories: dict[str, list[str]],
    force: bool = False,
    source: str = "session",
    agent_type: str | None = None,
) -> None:
    """Append a session entry to the pending summaries queue.

    Only appends when at least one significant category is detected,
    unless *force* is True (used when the AI gate has already decided).
    Guards against duplicates by session ID (transcript filename stem).

    Args:
        transcript_path: Path to the transcript JSONL file.
        project: The project name.
        categories: Detected categories mapping keys to excerpt lists.
        force: Skip the significance filter; queue unconditionally.
        source: Origin of the transcript — ``"session"`` or ``"subagent"``.
        agent_type: Subagent type (e.g. ``"Explore"``); only meaningful when
            *source* is ``"subagent"``.
    """
    all_keys = set(categories.keys())
    if not force:
        significant = {"error_fix", "research", "pattern"}
        if not (significant & all_keys):
            return

    pending_path = VAULT_ROOT / "pending_summaries.jsonl"
    session_id = transcript_path.stem

    entry: dict[str, object] = {
        "session_id": session_id,
        "transcript_path": str(transcript_path),
        "project": project,
        "categories": sorted(all_keys),
        "timestamp": datetime.now().isoformat(),
        "source": source,
    }
    if agent_type is not None:
        entry["agent_type"] = agent_type

    try:
        with open(pending_path, "a+", encoding="utf-8") as f:
            flock_exclusive(f)
            try:
                f.seek(0)
                for raw_line in f:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        existing = json.loads(line)
                        existing_id = (
                            existing.get("session_id")
                            or Path(existing.get("transcript_path", "")).stem
                        )
                        if existing_id == session_id:
                            return  # Already queued
                    except (json.JSONDecodeError, ValueError):
                        continue
                f.seek(0, 2)
                f.write(json.dumps(entry) + "\n")
            finally:
                funlock(f)
    except OSError:
        pass
```

- [ ] **Step 2: Verify vault_common.py is syntactically valid**

```bash
cd /Users/probello/Repos/parsidion
python3 -c "import sys; sys.path.insert(0, 'skills/parsidion/scripts'); import vault_common; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add skills/parsidion/scripts/vault_common.py
git commit -m "feat(vault_common): add shared transcript analysis and append_to_pending functions"
```

---

### Task 2: Update session_stop_hook.py to import from vault_common

**Files:**
- Modify: `skills/parsidion/scripts/session_stop_hook.py`

- [ ] **Step 1: Remove `_CATEGORIES`, `_CATEGORY_LABELS`, `parse_transcript_lines`, `detect_categories`, `append_to_pending` from `session_stop_hook.py` and replace with imports from `vault_common`**

Remove the five definitions (the two dicts and three functions).

Replace the existing references with imports from `vault_common`:
```python
# Shared transcript analysis functions (canonical implementation in vault_common)
parse_transcript_lines = vault_common.parse_transcript_lines
detect_categories = vault_common.detect_categories
append_to_pending = vault_common.append_to_pending
_CATEGORIES = vault_common.TRANSCRIPT_CATEGORIES
_CATEGORY_LABELS = vault_common.TRANSCRIPT_CATEGORY_LABELS
```

Also update `_CATEGORY_LABELS` references in `append_session_to_daily` to use the same dict (already aliased above, so no further changes needed if `_CATEGORY_LABELS` alias is present).

- [ ] **Step 2: Verify session_stop_hook.py is syntactically valid**

```bash
cd /Users/probello/Repos/parsidion
python3 -c "import sys; sys.path.insert(0, 'skills/parsidion/scripts'); import session_stop_hook; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Smoke-test with synthetic input**

```bash
cd /Users/probello/Repos/parsidion
echo '{"transcript_path": "/nonexistent/path.jsonl", "cwd": "/tmp"}' \
  | python3 skills/parsidion/scripts/session_stop_hook.py 2>/dev/null
```
Expected: `{}` (graceful exit on missing transcript)

- [ ] **Step 4: Commit**

```bash
git add skills/parsidion/scripts/session_stop_hook.py
git commit -m "refactor(session_stop_hook): import shared transcript functions from vault_common"
```

---

## Chunk 2: New subagent_stop_hook.py

### Task 3: Create subagent_stop_hook.py

**Files:**
- Create: `skills/parsidion/scripts/subagent_stop_hook.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""Claude Code SubagentStop hook — captures learnings from subagent transcripts.

Registered under the SubagentStop hook with ``async: true`` so it runs in the
background and never blocks the subagent. Reads the subagent's own transcript
(``agent_transcript_path``), detects learnable content using keyword heuristics,
and queues the transcript to ``~/ClaudeVault/pending_summaries.jsonl`` for
AI-powered summarisation by ``summarize_sessions.py``.

Differences from session_stop_hook.py:
- Uses ``agent_transcript_path`` (the subagent's transcript) not ``transcript_path``
- Reads ALL lines of the transcript (subagents are short; no 200-line cap)
- Uses ``agent_id`` as the deduplication key when available
- Skips daily-note update (too noisy for frequent subagent calls)
- Does NOT launch the summarizer (deferred to the next SessionEnd)
- Respects ``subagent_stop_hook.enabled`` config (default: true)
- Respects ``subagent_stop_hook.min_messages`` config (default: 3) to filter
  trivial subagents with only one or two assistant turns
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import vault_common  # noqa: E402

_LOG_PREFIX = "[subagent_stop_hook]"


def main() -> None:
    """Entry point: read SubagentStop JSON from stdin, analyse transcript, queue learnings."""
    try:
        raw_stdin = sys.stdin.read()
        input_data: dict[str, object] = json.loads(raw_stdin)
    except (json.JSONDecodeError, ValueError):
        print(f"{_LOG_PREFIX} ERROR: failed to parse stdin JSON", file=sys.stderr)
        sys.stdout.write("{}")
        return

    try:
        # Guard against recursive invocation
        if os.environ.get("CLAUDE_VAULT_STOP_ACTIVE"):
            print(f"{_LOG_PREFIX} skipping: recursive invocation detected", file=sys.stderr)
            sys.stdout.write("{}")
            return

        # Respect enabled config (default: true)
        if not vault_common.get_config("subagent_stop_hook", "enabled", True):
            print(f"{_LOG_PREFIX} disabled via config", file=sys.stderr)
            sys.stdout.write("{}")
            return

        agent_transcript_str = str(input_data.get("agent_transcript_path", ""))
        agent_id = str(input_data.get("agent_id", ""))
        agent_type = str(input_data.get("agent_type", "unknown"))
        cwd = str(input_data.get("cwd", ""))

        if not agent_transcript_str:
            print(f"{_LOG_PREFIX} skipping: no agent_transcript_path in input", file=sys.stderr)
            sys.stdout.write("{}")
            return

        agent_transcript = Path(agent_transcript_str)
        if not agent_transcript.is_file():
            print(
                f"{_LOG_PREFIX} skipping: agent transcript not found: {agent_transcript}",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        vault_common.ensure_vault_dirs()

        project: str = vault_common.get_project_name(cwd) if cwd else "unknown"
        print(
            f"{_LOG_PREFIX} agent_type={agent_type} project={project} "
            f"transcript={agent_transcript.name}",
            file=sys.stderr,
        )

        # Read ALL lines (subagent sessions are short)
        all_lines: list[str] = []
        try:
            with open(agent_transcript, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
        except OSError as exc:
            print(f"{_LOG_PREFIX} ERROR reading transcript: {exc}", file=sys.stderr)
            sys.stdout.write("{}")
            return

        assistant_texts = vault_common.parse_transcript_lines(all_lines)

        min_messages: int = vault_common.get_config("subagent_stop_hook", "min_messages", 3)
        if len(assistant_texts) < min_messages:
            print(
                f"{_LOG_PREFIX} skipping: only {len(assistant_texts)} assistant message(s) "
                f"(min_messages={min_messages})",
                file=sys.stderr,
            )
            sys.stdout.write("{}")
            return

        print(
            f"{_LOG_PREFIX} parsed {len(assistant_texts)} assistant message(s)",
            file=sys.stderr,
        )

        categories = vault_common.detect_categories(assistant_texts)
        cats_str = ", ".join(categories.keys()) or "none"
        print(f"{_LOG_PREFIX} detected categories: [{cats_str}]", file=sys.stderr)

        # Use agent_id as the deduplication key when available; fall back to stem
        dedup_path = agent_transcript
        if agent_id:
            # Create a synthetic path whose stem is the agent_id for dedup purposes
            dedup_path = agent_transcript.parent / f"{agent_id}.jsonl"

        vault_common.append_to_pending(
            transcript_path=dedup_path,
            project=project,
            categories=categories,
            source="subagent",
            agent_type=agent_type,
        )

        significant = {"error_fix", "research", "pattern"}
        if significant & set(categories.keys()):
            print(f"{_LOG_PREFIX} subagent queued for summarization", file=sys.stderr)
        else:
            print(
                f"{_LOG_PREFIX} subagent not queued (no significant categories)",
                file=sys.stderr,
            )

        sys.stdout.write("{}")

    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.stdout.write("{}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify syntax**

```bash
cd /Users/probello/Repos/parsidion
python3 -c "import sys; sys.path.insert(0, 'skills/parsidion/scripts'); import subagent_stop_hook; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Smoke-test with synthetic SubagentStop input**

```bash
cd /Users/probello/Repos/parsidion
echo '{"agent_transcript_path": "/nonexistent/agent.jsonl", "agent_id": "test-123", "agent_type": "Explore", "cwd": "/tmp"}' \
  | python3 skills/parsidion/scripts/subagent_stop_hook.py 2>/dev/null
```
Expected: `{}` (graceful exit on missing transcript)

- [ ] **Step 4: Commit**

```bash
git add skills/parsidion/scripts/subagent_stop_hook.py
git commit -m "feat(subagent_stop_hook): new SubagentStop hook captures subagent learnings"
```

---

## Chunk 3: Installer, config, and registration

### Task 4: Update install.py to register SubagentStop

**Files:**
- Modify: `install.py`

- [ ] **Step 1: Add `SubagentStop` to `_HOOK_SCRIPTS` and add `_HOOK_OPTIONS` dict**

In `_HOOK_SCRIPTS`, add:
```python
    "SubagentStop": "subagent_stop_hook.py",
```

Add a new `_HOOK_OPTIONS` dict immediately after `_HOOK_SCRIPTS`:
```python
# Per-event hook options merged into the hook handler entry.
# Keys match event names in _HOOK_SCRIPTS.
_HOOK_OPTIONS: dict[str, dict] = {
    "SubagentStop": {"async": True},
}
```

- [ ] **Step 2: Update `merge_hooks` to apply per-event options**

Inside the `for event, script_name in _HOOK_SCRIPTS.items():` loop in `merge_hooks`, change the `new_entry` construction to merge any extra options from `_HOOK_OPTIONS`:

```python
        hook_handler: dict = {
            "type": "command",
            "command": command,
            "timeout": 10000,
        }
        # Apply per-event options (e.g. async: true for SubagentStop)
        hook_handler.update(_HOOK_OPTIONS.get(event, {}))

        new_entry: dict = {
            "matcher": "",
            "hooks": [hook_handler],
        }
```

- [ ] **Step 3: Verify install.py is syntactically valid**

```bash
cd /Users/probello/Repos/parsidion
python3 -c "import install; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add install.py
git commit -m "feat(install): register SubagentStop hook with async:true"
```

---

### Task 5: Add subagent_stop_hook section to config template

**Files:**
- Modify: `skills/parsidion/templates/config.yaml`

- [ ] **Step 1: Add config section after `session_stop_hook` section**

```yaml
# Subagent stop hook (subagent_stop_hook.py)
subagent_stop_hook:
  enabled: true            # Set false to disable subagent transcript capture entirely
  min_messages: 3          # Minimum assistant message count; filters trivial subagents
  # Agent types to skip — vault-explorer and research-documentation-agent run constantly
  # and are part of the vault system itself; capturing them would be recursive noise.
  # Value is a comma-separated string (config parser limitation).
  excluded_agents: "vault-explorer,research-documentation-agent"
```

- [ ] **Step 2: Commit**

```bash
git add skills/parsidion/templates/config.yaml
git commit -m "feat(config): add subagent_stop_hook config section"
```

---

### Task 6: Sync to installed location and register hook

**Files:**
- Run: `uv run install.py --force --yes`

- [ ] **Step 1: Run the installer to sync scripts and register the hook**

```bash
cd /Users/probello/Repos/parsidion
uv run install.py --force --yes
```
Expected output includes: `Register hook SubagentStop` and `Updated ~/.claude/settings.json`

- [ ] **Step 2: Verify hook registered in settings.json**

```bash
python3 -c "
import json, pathlib
s = json.loads(pathlib.Path.home().joinpath('.claude/settings.json').read_text())
h = s['hooks'].get('SubagentStop', [])
print(json.dumps(h, indent=2))
"
```
Expected: entry with `async: true` and the `subagent_stop_hook.py` command

- [ ] **Step 3: Verify installed script exists**

```bash
ls ~/.claude/skills/parsidion/scripts/subagent_stop_hook.py
```
Expected: file present

- [ ] **Step 4: Smoke-test the installed script**

```bash
echo '{"agent_transcript_path": "/nonexistent/x.jsonl", "agent_id": "abc", "agent_type": "Explore", "cwd": "/tmp"}' \
  | uv run --no-project ~/.claude/skills/parsidion/scripts/subagent_stop_hook.py
```
Expected: `{}`

- [ ] **Step 5: Rebuild vault index**

```bash
uv run --no-project ~/.claude/skills/parsidion/scripts/update_index.py
```

- [ ] **Step 6: Final commit**

```bash
cd /Users/probello/Repos/parsidion
git add .
git commit -m "chore: post-install sync confirmation"
```
