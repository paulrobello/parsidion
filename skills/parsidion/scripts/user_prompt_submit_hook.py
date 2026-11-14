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
``parsidion.core.parsight_backend.parsight_search``). A failed availability probe is
negative-cached via a stamp file in ``~/.claude/logs`` for
``probe_cache_seconds``; a budget-burning failed search (daemon busy, e.g.
mid-reindex) writes its own cooldown stamp for the same window so subsequent
prompts skip retrieval instantly instead of re-burning the budget.

Config section: ``user_prompt_submit_hook`` (see ``parsidion.core.vault_schema``);
every key falls back to the default below when absent.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parsidion.core import parsight_backend
from parsidion.core.rule_triggers import load_rule_notes, match_rules
from parsidion.core.vault_hooks import get_project_name, write_hook_event
from parsidion.core.vault_path import resolve_vault, secure_log_dir
from parsidion.recall_context import (
    _build_context,
    _note_tags,
    load_hook_settings,
    set_stamp,
    stamp_fresh,
)

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
    # Per-prompt parsight search budget. Must stay below the 10s
    # UserPromptSubmit host kill (pi extension HOOK_TIMEOUT_USER_PROMPT_MS)
    # with room for interpreter startup, the ≤1s availability probe, and the
    # SIGTERM→SIGKILL grace (_SEARCH_KILL_GRACE_S).
    "recall_timeout_s": 7.0,
    "debug": False,
}

# SIGTERM→SIGKILL grace for the search child. Kept short so a wedged child
# cannot push total hook wall time past the host's 10s UserPromptSubmit kill.
_SEARCH_KILL_GRACE_S: float = 1.0

_SEARCH_COOLDOWN_STAMP_NAME = "parsidion-ups-search-cooldown"
# A failed search stamps the cooldown only when it burned at least this much
# wall time — a timeout burns the full recall_timeout_s by definition, while
# cheap fast failures (bad json, early exit) cost nothing and must not
# suppress recall for the window.
_SEARCH_COOLDOWN_MIN_BURN_S = 3.0

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


def _load_settings(vault: Path) -> dict[str, Any]:
    """Read the ``user_prompt_submit_hook`` section, defaulting per key."""
    return load_hook_settings(
        vault,
        section="user_prompt_submit_hook",
        defaults=_DEFAULTS,
        timeout_hi=float(_DEFAULTS["recall_timeout_s"]),
    )


def _probe_stamp_path() -> Path:
    """Negative-cache stamp for a failed parsight availability probe."""
    return secure_log_dir() / _PROBE_STAMP_NAME


def _search_cooldown_stamp_path() -> Path:
    """Negative-cache stamp for a budget-burning failed search."""
    return secure_log_dir() / _SEARCH_COOLDOWN_STAMP_NAME


def _term_overlap(prompt_tokens: set[str], note: dict[str, object]) -> int:
    """Count DISTINCT tokens shared between the prompt and title+tags+stem."""
    meta = " ".join(
        (str(note.get("title") or ""), str(note.get("stem") or ""), _note_tags(note))
    )
    return len(prompt_tokens & _tokens(meta))


@dataclass
class _RecallContext:
    """Per-invocation state shared by the run_recall stage helpers."""

    payload: dict
    started: float
    stages: dict[str, float] = field(default_factory=dict)
    prompt: str = ""
    cwd: str = ""
    vault: Path | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    matched_rules: list[dict[str, Any]] = field(default_factory=list)


def _mark(ctx: _RecallContext, name: str) -> None:
    """Record a cumulative stage mark (ms since run_recall started)."""
    ctx.stages[name] = round((time.perf_counter() - ctx.started) * 1000.0, 1)


def _stage_deltas(ctx: _RecallContext) -> dict[str, float]:
    """Convert cumulative stage marks into per-stage milliseconds."""
    deltas: dict[str, float] = {}
    prev = 0.0
    for name, cumulative in ctx.stages.items():
        deltas[name] = round(cumulative - prev, 1)
        prev = cumulative
    return deltas


def _swallow_debug(ctx: _RecallContext, stage: str, exc: BaseException) -> None:
    """Debug-gated swallow note on stderr (QA-009): stage + exception class.

    Fail-open control flow is unchanged; this only surfaces a swallowed
    stage failure when ``debug: true`` (which also sets ``PARSIDION_DEBUG``
    so parsight_backend's internal swallows become visible too). Untrusted
    note content never appears — the exception class name only.
    """
    if not ctx.settings.get("debug"):
        return
    print(
        f"[user_prompt_submit_hook] {stage} failed: {type(exc).__name__}",
        file=sys.stderr,
    )


def _emit(ctx: _RecallContext, notes: list[dict[str, object]]) -> dict:
    """Compose rules + recall into one bounded, framed injection."""
    if not notes and not ctx.matched_rules:
        return {}
    context = _build_context(
        notes, "this prompt", ctx.settings, rules=ctx.matched_rules or None
    )
    _mark(ctx, "format")
    if not context:
        return {}
    try:
        write_hook_event(
            hook="UserPromptSubmit",
            project=get_project_name(ctx.cwd),
            duration_ms=(time.perf_counter() - ctx.started) * 1000.0,
            vault=ctx.vault,
            notes_injected=len(notes) + len(ctx.matched_rules),
            rules_injected=len(ctx.matched_rules),
            chars=len(context),
            session_id=str(ctx.payload.get("session_id") or ""),
            stages_ms=_stage_deltas(ctx),
        )
    except Exception as exc:  # noqa: BLE001
        _swallow_debug(ctx, "hook-event", exc)
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }


def _match_rules_stage(ctx: _RecallContext, vault: Path) -> None:
    """Rules leg: trigger-scoped rule notes are push, not retrieval —
    they inject independent of parsight health, before the probe gate,
    so a daemon outage never suppresses a directive."""
    try:
        ctx.matched_rules = match_rules(load_rule_notes(vault), prompt=ctx.prompt)
    except Exception as exc:  # noqa: BLE001
        _swallow_debug(ctx, "rules", exc)
        ctx.matched_rules = []
    _mark(ctx, "rules")


def _probe_parsight(ctx: _RecallContext) -> bool:
    """Probe parsight availability for the search leg; never raises."""
    try:
        probe_ok = parsight_backend.resolve_parsight_backend(ctx.vault)
    except Exception as exc:  # noqa: BLE001
        _swallow_debug(ctx, "probe", exc)
        probe_ok = False
    _mark(ctx, "probe")
    return probe_ok


def _search_notes(ctx: _RecallContext, vault: Path) -> list[dict[str, object]]:
    """Search parsight and filter to notes sharing distinct prompt tokens."""
    search_started = time.monotonic()
    try:
        results = parsight_backend.parsight_search(
            ctx.prompt,
            top_k=ctx.settings["top_k"],
            vault=vault,
            timeout=ctx.settings["recall_timeout_s"],
            kill_grace_secs=_SEARCH_KILL_GRACE_S,
        )
    except Exception as exc:  # noqa: BLE001
        _swallow_debug(ctx, "search", exc)
        results = None
    _mark(ctx, "search")
    if (
        results is None
        and (time.monotonic() - search_started) >= _SEARCH_COOLDOWN_MIN_BURN_S
    ):
        set_stamp(_search_cooldown_stamp_path(), True)
    notes: list[dict[str, object]] = []
    if results:
        min_matches = ctx.settings["min_term_matches"]
        prompt_tokens = _tokens(ctx.prompt)
        if min_matches <= 0:
            notes = [r for r in results if isinstance(r, dict)]
        else:
            notes = [
                r
                for r in results
                if isinstance(r, dict)
                and _term_overlap(prompt_tokens, r) >= min_matches
            ]
    return notes


def run_recall(payload: dict) -> dict:
    """Full recall pipeline; returns the stdout dict ({}` or the injection).

    Pure function of *payload* — no stdin/stdout here. ``prompt``, ``cwd``
    and ``session_id`` payload keys are all optional. Never raises.
    """
    ctx = _RecallContext(payload={}, started=time.perf_counter())
    try:
        if not isinstance(payload, dict):
            payload = {}
        ctx.payload = payload
        ctx.prompt = str(payload.get("prompt") or "")
        ctx.cwd = str(payload.get("cwd") or os.getcwd())
        if is_noise_prompt(ctx.prompt):
            return {}

        try:
            vault = resolve_vault(cwd=ctx.cwd)
        except Exception:  # noqa: BLE001
            vault = resolve_vault()
        ctx.vault = vault
        _mark(ctx, "resolve_vault")
        ctx.settings = _load_settings(vault)
        if ctx.settings["debug"]:
            # Surface parsight_backend's internal swallows under the same
            # debug switch (QA-009); process-local, never leaves the hook.
            os.environ["PARSIDION_DEBUG"] = "1"
        _mark(ctx, "load_settings")

        if not ctx.settings["enabled"]:
            return {}
        if len(ctx.prompt) < ctx.settings["min_prompt_chars"]:
            return {}

        _match_rules_stage(ctx, vault)

        # Probe gate with negative cache: a recent failed probe skips both
        # the probe and the search silently.
        stamp = _probe_stamp_path()
        if stamp_fresh(stamp, ctx.settings["probe_cache_seconds"]):
            return {}
        if not _probe_parsight(ctx):
            set_stamp(stamp, True)
            return _emit(ctx, [])
        set_stamp(stamp, False)

        cooldown = _search_cooldown_stamp_path()
        if stamp_fresh(cooldown, ctx.settings["probe_cache_seconds"]):
            return _emit(ctx, [])

        notes = _search_notes(ctx, vault)
        if not notes and not ctx.matched_rules:
            return {}
        _mark(ctx, "filter")

        result = _emit(ctx, notes)
        if not result:
            return {}
        _mark(ctx, "event")

        if ctx.settings["debug"]:
            print(
                "[user_prompt_submit_hook] stages_ms="
                + json.dumps(ctx.stages, sort_keys=True),
                file=sys.stderr,
            )
        return result
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
