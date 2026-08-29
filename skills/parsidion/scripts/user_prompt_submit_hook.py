#!/usr/bin/env python3
"""Claude Code / Codex UserPromptSubmit hook: push vault recall into context.

On every user prompt this hook queries the vault via parsight's hybrid
retrieval and injects bounded note facts as ``additionalContext`` BEFORE the
model answers — retrieval is pushed, so the model never needs a tool call to
recall vault knowledge.

Shared contract (identical script registered by both Claude Code and Codex;
the omp/pi runtime mirrors it in TypeScript):

- stdin carries a JSON payload with optional ``prompt``, ``cwd`` and
  ``session_id`` keys (Codex sends no ``cwd`` — the script falls back to
  ``os.getcwd()``).
- stdout carries exactly one JSON object: ``{}`` when nothing is injected,
  else ``{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
  "additionalContext": <text>}}``.
- NEVER BLOCKS: malformed stdin, any exception, or any retrieval failure
  prints ``{}`` and exits 0. Diagnostics go to stderr only.

Retrieval is parsight-only (the embeddings backend's fastembed cold-load is
too slow per-prompt). parsight RRF scores gate by rank, so relevance is
decided by a distinct-token overlap gate between the prompt and each note's
title/tags/stem — ``min_score`` deliberately does not apply (see
``core.parsight_backend.parsight_search``). A failed availability probe is
negative-cached via a stamp file in ``~/.claude/logs`` for
``probe_cache_seconds``.

Config section: ``user_prompt_submit_hook`` (see ``core.vault_schema``);
every key falls back to the default below when absent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from core import parsight_backend
from core.vault_config import load_typed_config
from core.vault_hooks import get_project_name, write_hook_event
from core.vault_index import read_note_summary
from core.vault_path import resolve_vault, secure_log_dir
from session_start.context import UNTRUSTED_PREAMBLE

# Defaults for the shared ``user_prompt_submit_hook`` config section. Each is
# a per-key fallback: the typed section (Slice A schema) wins when present.
_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "top_k": 3,
    "max_chars": 1500,  # total body budget for the injected context
    "per_note_chars": 350,  # per-note excerpt budget
    "min_term_matches": 2,  # relevance gate; 0 disables the gate
    "min_prompt_chars": 9,  # shorter prompts skip retrieval ("continue" = 8 chars)
    "probe_cache_seconds": 300,
    "debug": False,
}

# ~15 high-frequency English words that carry no topical signal; excluded
# from the token-overlap gate so boilerplate prompt words cannot satisfy it.
_STOPWORDS = frozenset(
    (
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "what",
        "how",
        "why",
        "when",
        "was",
        "are",
        "you",
    )
)

# Noise-prompt filter, derived from the real prompt corpus (2026-08-29:
# 1,493 Claude Code session files, 2,054 unique prompts). Acks, goads,
# slash commands, menu selections, and harness artifacts carry no retrieval
# signal; genuine questions flow to the relevance gate. Applied before the
# vault is resolved so noise costs nothing.
_SKIP_EXACT: frozenset[str] = frozenset(
    {
        "continue",  # x185 in the corpus
        "yes",  # x72
        "ready",  # x46
        "push",  # x40
        "do it",  # x24
        "status",  # x13
        "do the follow up",  # x11 (+ variants x13)
        "do the follow ups",
        "do the followups",
        "deploy",  # x9
        "both",  # x5
        "not responding",  # x4
        "no",
        "ok",
        "okay",
        "go",
        "go on",
        "proceed",
        "more",
        "again",
        "next",
        "same",
        "done",
        "stop",
        "skip",
        # Cross-runtime goads (omp/pi/codex corpora 2026-08-29): all >=9
        # chars, so they pass the length gate and need explicit rules.
        "commit all",  # x31
        "commit and push",  # x20
        "commit all work",  # x8
        "commit and push all",  # x5
        "update changelog and commit",  # x5
        "do it all",  # x15
        "do all next steps",  # x4
        "try again",
        "whats next",  # x8
        "remove completed items from ideas.md",  # x11 routine housekeeping goad
    }
)
_SKIP_PREFIXES: tuple[str, ...] = (
    "[request interrupted",  # Claude interruption markers logged as user turns (x42)
    "goal set:",  # grind overseer work-loop goads (x8)
    "reply with exactly",  # grind reply-probe smokes (x7)
    "use the write tool to create ./perm_probe",  # file-creation probe (x7)
    "complete assignment thoroughly:",  # automated assignment runner (x7)
)
# Menu selections only ("1", "1,2,3", "2.", "3)") — deliberately NOT dates
# ("2026-08-29") or versions ("1.2.3"), which are real retrieval queries.
_SELECTION_RE = re.compile(r"^\d+(?:\s*,\s*\d+)*[.)]?$")
# Slash COMMANDS only ("/compact", "/work-loop until done"): a leading "/"
# followed by a command name and whitespace/end. Path-led prompts
# ("/etc/hosts is wrong") keep flowing to retrieval.
_SLASH_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][A-Za-z0-9_-]*(?:\s|$)")


def is_noise_prompt(prompt: str) -> bool:
    """True when *prompt* carries no retrieval signal and must not run."""
    text = prompt.strip()
    if not text or _SLASH_COMMAND_RE.match(text):
        return True
    lowered = text.lower()
    return bool(
        lowered in _SKIP_EXACT
        or lowered.startswith(_SKIP_PREFIXES)
        or _SELECTION_RE.match(text)
    )


_TOKEN_RE = re.compile(r"[a-z0-9]+")

_PROBE_STAMP_NAME = "parsidion-ups-probe"


def _tokens(text: str) -> set[str]:
    """Lowercase [a-z0-9]+ tokens, len>=3, minus the stopword list."""
    return {
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= 3 and tok not in _STOPWORDS
    }


def _load_settings(vault: Path) -> dict[str, object]:
    """Read the ``user_prompt_submit_hook`` section, defaulting per key.

    Tolerates the section being absent (or config load failing) so the hook
    never blocks on configuration problems.
    """
    settings: dict[str, object] = dict(_DEFAULTS)
    try:
        section = getattr(
            load_typed_config(vault=vault), "user_prompt_submit_hook", None
        )
    except Exception:  # noqa: BLE001
        return settings
    if section is None:
        return settings
    for key, default in _DEFAULTS.items():
        settings[key] = getattr(section, key, default)
    return settings


def _probe_stamp_path() -> Path:
    """Negative-cache stamp for a failed parsight availability probe."""
    return secure_log_dir() / _PROBE_STAMP_NAME


def _probe_stamp_fresh(stamp: Path, max_age_s: int) -> bool:
    """True when *stamp* exists and is younger than *max_age_s* seconds."""
    try:
        return (time.time() - stamp.stat().st_mtime) < max_age_s
    except OSError:
        return False


def _note_tags(note: dict[str, object]) -> str:
    """Space-joined string tags; non-list/​non-str shapes collapse to ""."""
    raw = note.get("tags")
    if not isinstance(raw, list):
        return ""
    return " ".join(t for t in raw if isinstance(t, str))


def _term_overlap(prompt_tokens: set[str], note: dict[str, object]) -> int:
    """Count DISTINCT tokens shared between the prompt and title+tags+stem."""
    meta = " ".join(
        (str(note.get("title") or ""), str(note.get("stem") or ""), _note_tags(note))
    )
    return len(prompt_tokens & _tokens(meta))


def _excerpt(note: dict[str, object], per_note_chars: int) -> str:
    """One-line excerpt: result ``summary`` if truthy, else read_note_summary."""
    summary = str(note.get("summary") or "").strip()
    if not summary:
        raw_path = str(note.get("path") or "")
        if raw_path:
            try:
                summary = read_note_summary(Path(raw_path)).strip()
            except Exception:  # noqa: BLE001
                summary = ""
    summary = " ".join(summary.split())  # collapse to a single line
    if len(summary) > per_note_chars:
        summary = summary[: per_note_chars - 1].rstrip() + "…"
    return summary


def _build_context(
    notes: list[dict[str, object]], vault: Path, settings: dict[str, Any]
) -> str:
    """Format matched notes into the bounded, untrusted-framed context body."""
    per_note_chars = int(settings["per_note_chars"])
    max_chars = int(settings["max_chars"])
    preamble = UNTRUSTED_PREAMBLE + "<content>\n"
    suffix = "\n</content>\n"
    if max_chars < len(preamble) + len(suffix):
        return ""
    lines = [f"Vault recall — {len(notes)} note(s) relevant to this prompt:"]
    for note in notes:
        title = str(note.get("title") or note.get("stem") or "untitled")
        folder = str(note.get("folder") or "")
        stem = str(note.get("stem") or "")
        loc = f"{folder}/{stem}" if folder else stem
        tags = ", ".join(t for t in _note_tags(note).split() if t)
        lines.append(f"- **{title}** [{loc}] ({tags})")
        lines.append(f"  {_excerpt(note, per_note_chars)}")
    body = "\n".join(lines)
    available_body_chars = max_chars - len(preamble) - len(suffix)
    if len(body) > available_body_chars:
        body = body[:available_body_chars]
    return preamble + body + suffix


def run_recall(payload: dict) -> dict:
    """Full recall pipeline; returns the stdout dict ({}` or the injection).

    Pure function of *payload* — no stdin/stdout here. ``prompt``, ``cwd``
    and ``session_id`` payload keys are all optional. Never raises.
    """
    started = time.perf_counter()
    stages: dict[str, float] = {}

    def _mark(name: str) -> None:
        stages[name] = round((time.perf_counter() - started) * 1000.0, 1)

    try:
        if not isinstance(payload, dict):
            payload = {}
        prompt = str(payload.get("prompt") or "")
        cwd = str(payload.get("cwd") or os.getcwd())
        if is_noise_prompt(prompt):
            return {}

        try:
            vault = resolve_vault(cwd=cwd)
        except Exception:  # noqa: BLE001
            vault = resolve_vault()
        _mark("resolve_vault")
        settings = _load_settings(vault)
        _mark("load_settings")

        if not bool(settings["enabled"]):
            return {}
        if len(prompt) < int(settings["min_prompt_chars"]):  # type: ignore[arg-type]
            return {}

        # Probe gate with negative cache: a recent failed probe skips both
        # the probe and the search silently.
        stamp = _probe_stamp_path()
        if _probe_stamp_fresh(stamp, int(settings["probe_cache_seconds"])):  # type: ignore[arg-type]
            return {}
        try:
            probe_ok = parsight_backend.resolve_parsight_backend(vault)
        except Exception:  # noqa: BLE001
            probe_ok = False
        _mark("probe")
        if not probe_ok:
            try:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                stamp.touch()
            except OSError:
                pass
            return {}
        try:
            stamp.unlink(missing_ok=True)
        except OSError:
            pass

        try:
            results = parsight_backend.parsight_search(
                prompt,
                top_k=int(settings["top_k"]),  # type: ignore[arg-type]
                vault=vault,  # type: ignore[arg-type]
            )
        except Exception:  # noqa: BLE001
            results = None
        _mark("search")

        notes: list[dict[str, object]] = []
        if results:
            min_matches = int(settings["min_term_matches"])  # type: ignore[arg-type]
            prompt_tokens = _tokens(prompt)
            if min_matches <= 0:
                notes = [r for r in results if isinstance(r, dict)]
            else:
                notes = [
                    r
                    for r in results
                    if isinstance(r, dict)
                    and _term_overlap(prompt_tokens, r) >= min_matches
                ]
        if not notes:
            return {}
        _mark("filter")

        context = _build_context(notes, vault, settings)
        _mark("format")
        if not context:
            return {}

        try:
            write_hook_event(
                hook="UserPromptSubmit",
                project=get_project_name(cwd),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                vault=vault,
                notes_injected=len(notes),
                chars=len(context),
                session_id=str(payload.get("session_id") or ""),
            )
        except Exception:  # noqa: BLE001
            pass  # observability is best-effort
        _mark("event")

        if bool(settings["debug"]):
            print(
                "[user_prompt_submit_hook] stages_ms="
                + json.dumps(stages, sort_keys=True),
                file=sys.stderr,
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
    except Exception as exc:  # noqa: BLE001 -- never-block guarantee
        print(f"[user_prompt_submit_hook] recall skipped: {exc}", file=sys.stderr)
        return {}


def main() -> int:
    """Entry point: stdin JSON -> run_recall -> one JSON object -> exit 0."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception as exc:  # noqa: BLE001
        print(f"[user_prompt_submit_hook] malformed stdin: {exc}", file=sys.stderr)
        payload = {}
    try:
        result = run_recall(payload)
    except Exception as exc:  # noqa: BLE001 -- absolute never-block guarantee
        print(f"[user_prompt_submit_hook] unexpected failure: {exc}", file=sys.stderr)
        result = {}
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
