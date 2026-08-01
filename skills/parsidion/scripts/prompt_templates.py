"""Versioned prompt-template loader with a strict variable contract (ENH-008).

Templates live as files under ``templates/prompts/`` with a small YAML
frontmatter (``id``, ``version``, ``variables``, ``description``, optional
``syntax``) plus a body. The body uses one of two substitution syntaxes:

- ``syntax: format`` (default) — ``str.format``-style ``{var}`` placeholders.
  Literal braces in the body must be escaped as ``{{`` / ``}}``.
- ``syntax: template`` — ``string.Template`` ``$var`` placeholders. Used by
  prompts that contain literal ``{`` / ``}`` (e.g. JSON examples) so escaping
  is not needed. This is the syntax of the two legacy summarizer prompts.

The strict variable contract is the load-bearing part. ``render`` raises
:class:`PromptError` when:

- a declared variable is missing from the call (the classic silent-empty
  substitution that hides regressions), or
- an extra, undeclared variable is passed (catches typos like ``projectt=``).

Both checks run against the template's declared ``variables`` list, so a
template and its callers cannot drift unnoticed. A template with no
``variables`` list (legacy) skips the undeclared-variable check but still
raises on a missing referenced placeholder — this keeps the two pre-existing
summarizer prompts working while they migrate.

Stdlib-only — imported by ``session_start_hook`` (select-notes prompt), so it
is held to the same stdlib-only contract as the ``core/`` package and is
covered by ``tests/test_stdlib_only.py``.
"""

from __future__ import annotations

import string
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

# ARC-001: imported directly from core.* instead of the vault_common facade.
from core.vault_index import parse_frontmatter
from core.vault_path import resolve_templates_dir

__all__ = [
    "PromptError",
    "PromptTemplate",
    "load_prompt",
    "render",
    "render_raw",
    "reset_cache",
    "PROMPT_VERSION",
]


class PromptError(RuntimeError):
    """A template could not be loaded or rendered.

    Carries the ``prompt_id`` so callers (and tests) can distinguish a missing
    template from a rendering bug.
    """


@dataclass(frozen=True)
class PromptTemplate:
    """A loaded prompt template: parsed frontmatter plus the raw body.

    Attributes:
        id: The template's canonical id (frontmatter ``id``).
        version: Semantic version string (frontmatter ``version``).
        variables: Declared variable names (frontmatter ``variables``). Empty
            for legacy templates that carry no declaration.
        description: Human-readable summary (frontmatter ``description``).
        syntax: ``"format"`` (str.format) or ``"template"`` (string.Template).
        body: The raw template body (frontmatter stripped).
        source_path: Path to the template file (for error messages).
    """

    id: str
    version: str
    variables: tuple[str, ...]
    description: str
    syntax: str
    body: str
    source_path: Path
    # Pre-parsed engine object — string.Template for ``template`` syntax, None
    # for ``format`` (str.format is called on the body directly). Cached so
    # repeated renders do not re-parse.
    _engine: object = field(default=None, repr=False, compare=False)

    @property
    def version_stamp(self) -> str:
        """The ``<id>@<version>`` stamp written into note frontmatter."""
        return f"{self.id}@{self.version}"


#: The directory holding template files, resolved once per process via
#: :func:`vault_common.resolve_templates_dir`. Tests reset the cache after
#: monkeypatching the resolver.
def _templates_dir() -> Path:
    return resolve_templates_dir() / "prompts"


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Split a template file into ``(frontmatter_dict, body)``.

    Reuses :func:`vault_common.parse_frontmatter` for value parsing so there is
    exactly one frontmatter parser in the codebase. Returns ``({}, text)`` when
    the file has no opening ``---`` block (legacy templates), so the loader is
    backward-compatible with the two pre-existing summarizer prompts.

    The body is everything after the closing ``---`` line. Exactly one leading
    newline (the separator between the closer and the body) is stripped, so a
    template written as::

        ---
        id: x
        ---
        Body starts here.

    renders with body ``"Body starts here.\\n"`` — no spurious leading newline.
    Byte-identical rendering against the pre-externalization f-strings depends
    on this.
    """
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    # Locate the opening delimiter (first non-blank line == "---").
    start: int | None = None
    for i, ln in enumerate(lines):
        if ln.strip() == "":
            continue
        if ln.strip() == "---":
            start = i
        break
    if start is None:
        return {}, text
    end: int | None = None
    for j in range(start + 1, len(lines)):
        if lines[j].strip() == "---":
            end = j
            break
    if end is None:
        return {}, text
    fm_text = "".join(lines[start : end + 1])
    body = "".join(lines[end + 1 :])
    # Strip exactly one leading newline (the separator after the closer).
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    fm = parse_frontmatter(fm_text)
    if not isinstance(fm, dict):
        return {}, text
    return fm, body


def _coerce_variables(raw: object) -> tuple[str, ...]:
    """Normalize a frontmatter ``variables`` value into a tuple of names."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        # Allow ``variables: a, b`` (single-line form).
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(s).strip() for s in raw if str(s).strip())
    return ()


@cache
def load_prompt(prompt_id: str) -> PromptTemplate:
    """Load and cache the template identified by *prompt_id*.

    Resolution mirrors :func:`vault_common.resolve_templates_dir`: the sibling
    ``templates/prompts/`` directory next to this script (repo source layout)
    or the installed ``~/.claude/skills/parsidion/templates`` location. The
    file is named ``<prompt_id>.md``.

    Raises:
        PromptError: The template file is missing or its frontmatter lacks an
            ``id``/``version``.
    """
    path = _templates_dir() / f"{prompt_id}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(
            f"prompt template {prompt_id!r} not found at {path}: {exc}"
        ) from exc
    fm, body = _parse_frontmatter(raw)
    tpl_id = str(fm.get("id", "")).strip()
    if not tpl_id:
        raise PromptError(
            f"prompt template {prompt_id!r} at {path} has no 'id' in frontmatter"
        )
    version = str(fm.get("version", "")).strip()
    if not version:
        raise PromptError(
            f"prompt template {prompt_id!r} at {path} has no 'version' in frontmatter"
        )
    variables = _coerce_variables(fm.get("variables"))
    description = str(fm.get("description", "")).strip()
    syntax = str(fm.get("syntax", "format")).strip().lower()
    if syntax not in ("format", "template"):
        raise PromptError(
            f"prompt template {prompt_id!r} at {path} has unsupported "
            f"syntax {syntax!r} (expected 'format' or 'template')"
        )
    engine: object = None
    if syntax == "template":
        engine = string.Template(body)
    return PromptTemplate(
        id=tpl_id,
        version=version,
        variables=variables,
        description=description,
        syntax=syntax,
        body=body,
        source_path=path,
        _engine=engine,
    )


def render(prompt_id: str, **variables: object) -> str:
    """Render the template *prompt_id* with *variables*.

    Raises :class:`PromptError` when a declared variable is missing from the
    call or an undeclared one is passed. This bidirectional check is the
    load-bearing part — a silently-empty substitution is precisely how a prompt
    degrades without anyone noticing.
    """
    tpl = load_prompt(prompt_id)
    declared = set(tpl.variables)
    if declared:
        missing = declared - set(variables)
        if missing:
            raise PromptError(
                f"prompt {prompt_id!r} is missing required variable(s): "
                f"{sorted(missing)}"
            )
        extra = set(variables) - declared
        if extra:
            raise PromptError(
                f"prompt {prompt_id!r} got undeclared variable(s): {sorted(extra)}"
            )
    try:
        if tpl.syntax == "template":
            assert tpl._engine is not None
            return string.Template(tpl.body).substitute(**variables)  # type: ignore[arg-type]
        return tpl.body.format(**variables)
    except KeyError as exc:
        # A referenced placeholder was not supplied — only reachable for
        # legacy templates with no declared variable list.
        raise PromptError(
            f"prompt {prompt_id!r} references undefined variable {exc} "
            f"(declared variables: {list(tpl.variables) or '<none>'})"
        ) from exc
    except (ValueError, IndexError) as exc:
        raise PromptError(f"prompt {prompt_id!r} failed to render: {exc}") from exc


def render_raw(prompt_id: str) -> str:
    """Return the raw body of *prompt_id* without substitution (for tests/docs)."""
    return load_prompt(prompt_id).body


def reset_cache() -> None:
    """Clear the parsed-template cache.

    Tests call this after monkeypatching ``resolve_templates_dir`` or editing
    template files on disk so the next :func:`load_prompt` re-reads.
    """
    load_prompt.cache_clear()


#: Convenience: the ``<id>@<version>`` stamp for a prompt. Callers that stamp
#: notes (the summarizer) use this rather than reconstructing the format.
def PROMPT_VERSION(prompt_id: str) -> str:  # noqa: N802 - constant-style name
    """Return the ``<id>@<version>`` stamp for *prompt_id*."""
    return load_prompt(prompt_id).version_stamp
