#!/usr/bin/env python3
"""Claude Code PreToolUse hook: file-scoped vault recall on Read/Edit.

Parsidion injects at session start and prompt submit, but both retrieval
surfaces are pull-only when the agent opens a file: nothing recalls the
Debugging note covering exactly the file about to be read or edited. This
hook pushes file-scoped recall at that moment — the agent never needs a
tool call to see it.

Contract (registered under PreToolUse with matcher ``Read|Edit`` for
Claude Code and ``apply_patch`` for Codex):

- stdin carries a JSON payload with ``tool_name``, ``tool_input``
  (``file_path`` is extracted from it; Codex ``apply_patch`` instead
  carries a V4A patch under ``tool_input.command``, whose first
  Add/Update/Move-to target is used) and the common ``cwd`` /
  ``session_id`` keys.
- stdout carries exactly one JSON object: ``{}`` when nothing is injected,
  else ``{"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "additionalContext": <text>}}`` (supported by the PreToolUse output
  schema of Claude Code >= 2.x).
- NEVER BLOCKS: malformed stdin, any exception, or any retrieval failure
  prints ``{}`` and exits 0. Diagnostics go to stderr only.

Selection runs two legs, both scoped to the file being opened:

- **Local note_index scan** (always, ~ms): metadata rows scored by a
  distinct-token overlap gate between file-derived tokens (basename stem
  + project name) and each note's title/tags/stem.
- **parsight semantic search** (optional, ``parsight: true``): the same
  ``parsight_search`` the prompt-submit hook uses, with a file-derived
  query, so a note whose title shares no token with the filename is still
  recalled. Gated by the availability probe; a failed probe is
  negative-cached for ``cache_seconds``.

Results (including "nothing found") are cached per file for
``cache_seconds`` so re-reads of the same file are free — the cache lives
across hook invocations in ``~/.claude/logs/parsidion-ptu-cache/``.

Config section: ``pre_tool_use_hook`` (see ``core.vault_schema``); every
key falls back to the default below when absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from core import parsight_backend
from core.rule_triggers import load_rule_notes, match_rules
from core.vault_config import clamp_timeout, load_typed_config
from core.vault_hooks import get_project_name, write_hook_event
from core.vault_index import load_note_index_metadata, read_note_summary
from core.vault_path import is_path_inside_vault, resolve_vault, secure_log_dir
from session_start.context import UNTRUSTED_PREAMBLE

# Defaults for the ``pre_tool_use_hook`` config section. Each is a per-key
# fallback: the typed section (Slice A schema) wins when present.
_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "top_k": 3,
    "max_chars": 1500,  # total body budget for the injected context
    "per_note_chars": 350,  # per-note excerpt budget
    "min_term_matches": 2,  # relevance gate; 0 disables the gate
    "cache_seconds": 300,  # per-file result cache + probe negative-cache
    "parsight": True,  # run the semantic leg when the daemon is available
    # Per-file parsight search budget. Must stay below the 10s PreToolUse
    # host kill with room for interpreter startup, the availability probe,
    # and the SIGTERM→SIGKILL grace (_SEARCH_KILL_GRACE_S). Tighter than
    # the prompt-submit hook's 7s: this runs on every Read/Edit, not once
    # per prompt.
    "recall_timeout_s": 4.0,
    "debug": False,
}

_SEARCH_KILL_GRACE_S: float = 1.0

_PROBE_STAMP_NAME = "parsidion-ptu-probe"
_CACHE_DIR_NAME = "parsidion-ptu-cache"
# When the per-file cache holds more entries than this, oldest-by-mtime
# entries are pruned on write (best-effort) so the directory stays bounded.
_CACHE_MAX_ENTRIES = 512

# Tools whose ``tool_input`` carries the target file in ``file_path``
# (``notebook_path`` for NotebookEdit). The settings.json matcher narrows
# registration to Read|Edit; this set keeps the script itself correct if a
# user widens the matcher.
_FILE_PATH_TOOLS = frozenset({"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"})

# Codex apply_patch carries a V4A patch under tool_input.command; the files it
# touches live in the patch headers. Only Add/Update/Move-to matter for recall
# (Delete targets are being removed, not worked on). Patch paths are
# repo-relative and resolve against the payload cwd.
_PATCH_TARGET_RE = re.compile(r"^\*\*\* (?:Add|Update) File: (.+)$", re.MULTILINE)
_PATCH_MOVE_RE = re.compile(r"^\*\*\* Move to: (.+)$", re.MULTILINE)


def _patch_targets(command: str) -> list[str]:
    """V4A patch target paths from an apply_patch body, in patch order.

    Shallow line scan (mirrors parsight's codex hook): Add/Update headers
    plus Move-to destinations; Delete targets are deliberately skipped.
    """
    targets = _PATCH_TARGET_RE.findall(command)
    targets.extend(_PATCH_MOVE_RE.findall(command))
    return targets


# Words so common in directory paths that they carry no topical signal
# when deriving tokens from a file location.
_PATH_NOISE_TOKENS = frozenset(
    (
        "src",
        "lib",
        "test",
        "tests",
        "scripts",
        "core",
        "app",
        "apps",
        "pkg",
        "cmd",
        "internal",
        "components",
        "utils",
        "users",
        "repos",
        "home",
        "tmp",
    )
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Lowercase [a-z0-9]+ tokens, len>=3."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) >= 3}


def _file_tokens(file_path: Path, project: str) -> set[str]:
    """Tokens identifying the file: basename stem, split on separators.

    The project name joins the token set (a note tagged with the project
    counts as relevant), but path-segment noise words are excluded so
    ``src/lib/utils`` cannot satisfy the gate by itself.
    """
    tokens = _tokens(file_path.stem.replace("-", "_").replace(".", "_"))
    tokens |= _tokens(str(project))
    for segment in file_path.parent.parts[-2:]:
        tokens |= {tok for tok in _tokens(segment) if tok not in _PATH_NOISE_TOKENS}
    return tokens


def _file_query(file_path: Path, project: str) -> str:
    """Natural-language query handed to the parsight semantic leg."""
    return f"{file_path.stem.replace('-', ' ').replace('_', ' ')} {project}"


def _load_settings(vault: Path) -> dict[str, object]:
    """Read the ``pre_tool_use_hook`` section, defaulting per key.

    Tolerates the section being absent (or config load failing) so the
    hook never blocks on configuration problems.
    """
    settings: dict[str, object] = dict(_DEFAULTS)
    try:
        section = getattr(load_typed_config(vault=vault), "pre_tool_use_hook", None)
    except Exception:  # noqa: BLE001
        return settings
    if section is None:
        return settings
    for key, default in _DEFAULTS.items():
        settings[key] = getattr(section, key, default)
    # NaN/negative would reach communicate(timeout=...) as "no timeout"
    # (SEC-024 shape); clamp regardless of source. The ceiling keeps the
    # parsight leg inside the 10s PreToolUse host kill with startup +
    # probe + kill-grace headroom.
    settings["recall_timeout_s"] = clamp_timeout(
        settings["recall_timeout_s"],  # type: ignore[arg-type]
        default=_DEFAULTS["recall_timeout_s"],
        lo=1.0,
        hi=5.0,
    )
    return settings


def _extract_file_path(
    tool_name: object, tool_input: object, cwd: str = ""
) -> Path | None:
    """The file a Read/Edit-style call is about to touch, or None.

    Claude-style tools name the file in ``file_path`` (``notebook_path``
    for NotebookEdit). Codex ``apply_patch`` embeds a V4A patch under
    ``tool_input.command``; its first Add/Update/Move-to target is used,
    resolved against the payload ``cwd`` (codex patch paths are
    repo-relative). Multi-file patches recall for the first target only.
    Anything but a plain string path yields None — the payload is external
    input, never trusted to be well-shaped.
    """
    if isinstance(tool_name, str) and tool_name == "apply_patch":
        if not isinstance(tool_input, dict):
            return None
        command = tool_input.get("command")
        if not isinstance(command, str):
            return None
        targets = _patch_targets(command)
        if not targets:
            return None
        raw = targets[0].strip()
        candidate = Path(raw)
        if not candidate.is_absolute() and cwd:
            candidate = Path(cwd) / candidate
        try:
            return candidate.expanduser()
        except (OSError, ValueError, RuntimeError):
            return None
    if not isinstance(tool_name, str) or tool_name not in _FILE_PATH_TOOLS:
        return None
    if not isinstance(tool_input, dict):
        return None
    raw = tool_input.get("file_path")
    if not isinstance(raw, str) or not raw.strip():
        raw = tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(raw).expanduser()
    except (OSError, ValueError, RuntimeError):
        return None


def _cache_dir() -> Path:
    return secure_log_dir() / _CACHE_DIR_NAME


def _cache_key(file_path: Path) -> Path:
    digest = hashlib.sha1(str(file_path).encode("utf-8", "replace")).hexdigest()
    return _cache_dir() / f"{digest}.json"


def _cache_read(key: Path, cache_seconds: int) -> str | None:
    """Cached context for *key*, or None on miss/expiry. "" caches a miss."""
    try:
        raw = json.loads(key.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    saved_at = raw.get("saved_at")
    if not isinstance(saved_at, (int, float)):
        return None
    if (time.time() - saved_at) >= cache_seconds:
        return None
    context = raw.get("context")
    return context if isinstance(context, str) else ""


def _cache_write(key: Path, context: str) -> None:
    """Persist the injection outcome (possibly "") under *key*, best-effort."""
    try:
        cache_dir = key.parent
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = key.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"saved_at": time.time(), "context": context}),
            encoding="utf-8",
        )
        os.replace(tmp, key)
        _cache_prune(cache_dir)
    except OSError:
        pass  # cache is best-effort; a failed write must never block


def _cache_prune(cache_dir: Path, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
    """Drop the oldest cache entries past *max_entries* (best-effort)."""
    try:
        entries = list(cache_dir.glob("*.json"))
        if len(entries) <= max_entries:
            return
        entries.sort(key=lambda p: p.stat().st_mtime)
        for stale in entries[: len(entries) - max_entries]:
            stale.unlink(missing_ok=True)
    except OSError:
        pass


def _probe_stamp_path() -> Path:
    """Negative-cache stamp for a failed parsight availability probe."""
    return secure_log_dir() / _PROBE_STAMP_NAME


def _stamp_fresh(stamp: Path, max_age_s: int) -> bool:
    """True when *stamp* exists and is younger than *max_age_s* seconds."""
    try:
        return (time.time() - stamp.stat().st_mtime) < max_age_s
    except OSError:
        return False


def _note_tags(note: dict[str, object]) -> str:
    """Space-joined string tags; non-list/non-str shapes collapse to ""."""
    raw = note.get("tags")
    if isinstance(raw, list):
        return " ".join(t for t in raw if isinstance(t, str))
    if isinstance(raw, str):
        return " ".join(t.strip() for t in raw.split(",") if t.strip())
    return ""


def _term_overlap(file_tokens: set[str], note: dict[str, object]) -> int:
    """Count DISTINCT tokens shared between the file and title+tags+stem."""
    meta = " ".join(
        (str(note.get("title") or ""), str(note.get("stem") or ""), _note_tags(note))
    )
    return len(file_tokens & _tokens(meta))


def _excerpt(note: dict[str, object], per_note_chars: int) -> str:
    """One-line excerpt: row ``summary`` if truthy, else read_note_summary."""
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
    notes: list[dict[str, object]],
    file_path: Path,
    settings: dict[str, Any],
    rules: list[dict[str, object]] | None = None,
) -> str:
    """Format matched notes into the bounded, untrusted-framed context body.

    Triggered ``type: rule`` notes lead the body when present — directives,
    not recall — and share the same char budget. Each rule line names the
    triggers that fired.
    """
    per_note_chars = int(settings["per_note_chars"])
    max_chars = int(settings["max_chars"])
    preamble = UNTRUSTED_PREAMBLE + "<content>\n"
    suffix = "\n</content>\n"
    available_body_chars = max_chars - len(preamble) - len(suffix)
    if available_body_chars < 1:
        return ""
    lines: list[str] = []
    if rules:
        lines.append(f"Vault rules — {len(rules)} rule(s) triggered:")
        for rule in rules:
            title = str(rule.get("title") or rule.get("stem") or "untitled")
            folder = str(rule.get("folder") or "")
            stem = str(rule.get("stem") or "")
            loc = f"{folder}/{stem}" if folder else stem
            matched = rule.get("matched")
            matched_list = matched if isinstance(matched, list) else []
            trig = ", ".join(str(t) for t in matched_list)
            lines.append(f"- **{title}** [{loc}] (triggered: {trig})")
            lines.append(f"  {_excerpt(rule, per_note_chars)}")
        lines.append("")
    if notes:
        lines.append(
            f"Vault recall — {len(notes)} note(s) relevant to {file_path.name}:"
        )
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


def _merge_notes(
    local: list[dict[str, object]], semantic: list[dict[str, object]]
) -> list[dict[str, object]]:
    """Union both legs by stem; local (exact-gated) rows win on duplicates."""
    merged: dict[str, dict[str, object]] = {}
    for note in (*local, *semantic):
        stem = str(note.get("stem") or "")
        if stem and stem not in merged:
            merged[stem] = note
    return list(merged.values())


def _load_local_matches(
    vault: Path, file_tokens: set[str], settings: dict[str, Any]
) -> list[dict[str, object]]:
    """Score note_index metadata rows against the file's tokens."""
    try:
        rows = load_note_index_metadata(vault=vault)
    except Exception:  # noqa: BLE001
        rows = None
    if not rows:
        return []
    min_matches = int(settings["min_term_matches"])  # type: ignore[arg-type]
    scored: list[tuple[int, float, dict[str, object]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        overlap = _term_overlap(file_tokens, row)
        if min_matches > 0 and overlap < min_matches:
            continue
        mtime = row.get("mtime")
        scored.append(
            (
                overlap,
                float(mtime) if isinstance(mtime, (int, float)) else 0.0,
                row,
            )
        )
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[: int(settings["top_k"])]]


def _load_semantic_matches(
    vault: Path,
    query: str,
    settings: dict[str, Any],
) -> list[dict[str, object]]:
    """parsight semantic leg, gated by the availability probe + cooldown."""
    if not bool(settings["parsight"]):
        return []
    stamp = _probe_stamp_path()
    if _stamp_fresh(stamp, int(settings["cache_seconds"])):  # type: ignore[arg-type]
        return []
    try:
        probe_ok = parsight_backend.resolve_parsight_backend(vault)
    except Exception:  # noqa: BLE001
        probe_ok = False
    if not probe_ok:
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.touch()
        except OSError:
            pass
        return []
    try:
        stamp.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        results = parsight_backend.parsight_search(
            query,
            top_k=int(settings["top_k"]),  # type: ignore[arg-type]
            vault=vault,  # type: ignore[arg-type]
            timeout=float(settings["recall_timeout_s"]),  # type: ignore[arg-type]
            kill_grace_secs=_SEARCH_KILL_GRACE_S,
        )
    except Exception:  # noqa: BLE001
        results = None
    if not results:
        return []
    # No distinct-token gate here: the semantic leg exists to recall notes
    # whose title shares no token with the filename, and parsight's RRF
    # ranking is the relevance decision (same reasoning as min_score not
    # applying on the prompt-submit path).
    return [r for r in results if isinstance(r, dict)]


def run_injection(payload: dict) -> dict:
    """Full injection pipeline; returns the stdout dict ({} or the injection).

    Pure function of *payload* — no stdin/stdout here. Never raises.
    """
    started = time.perf_counter()
    stages: dict[str, float] = {}

    def _mark(name: str) -> None:
        stages[name] = round((time.perf_counter() - started) * 1000.0, 1)

    def _stage_deltas() -> dict[str, float]:
        deltas: dict[str, float] = {}
        prev = 0.0
        for name, cumulative in stages.items():
            deltas[name] = round(cumulative - prev, 1)
            prev = cumulative
        return deltas

    try:
        if not isinstance(payload, dict):
            payload = {}
        cwd = str(payload.get("cwd") or os.getcwd())
        file_path = _extract_file_path(
            payload.get("tool_name"), payload.get("tool_input"), cwd=cwd
        )
        if file_path is None:
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

        # Reading a vault note needs no vault recall about itself; also
        # keeps SEC-020-style path escapes out of the retrieval path.
        try:
            if is_path_inside_vault(file_path.resolve(), vault.resolve()):
                return {}
        except OSError:
            return {}

        # Per-file result cache: re-reads of the same file are free, and a
        # cached "" (nothing found) is served too.
        cache_key = _cache_key(file_path)
        cached = _cache_read(cache_key, int(settings["cache_seconds"]))  # type: ignore[arg-type]
        _mark("cache")
        if cached is not None:
            if not cached:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": cached,
                }
            }

        project = get_project_name(str(file_path.parent))
        file_tokens = _file_tokens(file_path, project)
        query = _file_query(file_path, project)

        # Rules leg: match triggers against the file path — push, not
        # retrieval, and independent of the parsight leg's health. Matched
        # rules ride the per-file result cache like the recall body.
        try:
            matched_rules = match_rules(load_rule_notes(vault), path=str(file_path))
        except Exception:  # noqa: BLE001
            matched_rules = []
        _mark("rules")

        local = _load_local_matches(vault, file_tokens, settings)
        _mark("local_scan")
        semantic = _load_semantic_matches(vault, query, settings)
        _mark("semantic_search")
        notes = _merge_notes(local, semantic)[: int(settings["top_k"])]  # type: ignore[arg-type]
        if not notes and not matched_rules:
            _cache_write(cache_key, "")
            return {}

        context = _build_context(
            notes, file_path, settings, rules=matched_rules or None
        )
        _mark("format")
        _cache_write(cache_key, context)
        if not context:
            return {}

        try:
            write_hook_event(
                hook="PreToolUse",
                project=get_project_name(cwd),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                vault=vault,
                notes_injected=len(notes) + len(matched_rules),
                rules_injected=len(matched_rules),
                chars=len(context),
                session_id=str(payload.get("session_id") or ""),
                stages_ms=_stage_deltas(),
            )
        except Exception:  # noqa: BLE001
            pass  # observability is best-effort
        _mark("event")

        if bool(settings["debug"]):
            print(
                "[pre_tool_use_hook] stages_ms=" + json.dumps(stages, sort_keys=True),
                file=sys.stderr,
            )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": context,
            }
        }
    except Exception as exc:  # noqa: BLE001 -- never-block guarantee
        print(f"[pre_tool_use_hook] recall skipped: {exc}", file=sys.stderr)
        return {}


def main() -> int:
    """Entry point: stdin JSON -> run_injection -> one JSON object -> exit 0."""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception as exc:  # noqa: BLE001
        print(f"[pre_tool_use_hook] malformed stdin: {exc}", file=sys.stderr)
        payload = {}
    try:
        result = run_injection(payload)
    except Exception as exc:  # noqa: BLE001 -- absolute never-block guarantee
        print(f"[pre_tool_use_hook] unexpected failure: {exc}", file=sys.stderr)
        result = {}
    sys.stdout.write(json.dumps(result) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
