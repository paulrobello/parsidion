"""Per-prompt evaluators for the prompt-eval harness (ENH-008, board item #3).

Stdlib-only (no ``rich``/``pyyaml`` at import) so the render/parse/score logic
is unit-testable in the numpy-free ``make test`` suite. The rich/CLI driver
(``prompt_eval_run.py``) imports :data:`evaluators.EVALUATORS` and supplies the
AI call, caching, and display.

Each evaluator knows how to:

- :meth:`load_cases` — read its golden cases from
  ``tests/fixtures/prompts/golden/<id>/`` (input shape is prompt-specific).
- :meth:`render` — render the prompt via :func:`prompt_templates.render`. That
  function's strict bidirectional variable check is the load-bearing gate: an
  adapter whose variables drift from the template's declared ``variables`` list
  raises ``PromptError`` — caught without an AI call.
- :meth:`parse` — extract a structured dict from the model's raw output.
- :meth:`score` — score the parsed output against the case's ``expected.yaml`` →
  ``(0-100, {check: fraction})``.

``prompt_templates.render`` dispatches both syntaxes (``template`` ``$var`` and
``format`` ``{var}``), so adapters just pass the right variables.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# Make the skills scripts importable (prompt_templates, note_schema, vault_common)
# — matches the sys.path manipulation in prompt_eval_run.py.
_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = str(_HERE.parents[2] / "skills" / "parsidion" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
_FIXTURE_ROOT = _HERE.parents[2] / "tests" / "fixtures" / "prompts" / "golden"


@dataclass
class CaseInput:
    """One golden input bundle + its expected characteristics."""

    case_id: str
    inputs: dict[str, str]
    expected: dict[str, Any]


@dataclass
class ScoredCase:
    """Scored result for one case under one prompt+model."""

    case_id: str
    prompt_id: str = ""
    prompt_version: str = ""
    model: str = ""
    score: float = 0.0
    checks: dict[str, float] = field(default_factory=dict)
    cached: bool = False
    error: str = ""
    raw_output: str = field(default="", repr=False)


class PromptEvaluator(Protocol):
    """The per-prompt contract the driver dispatches over."""

    prompt_id: str

    @property
    def version_stamp(self) -> str: ...

    def load_cases(self, limit: int | None = None) -> list[CaseInput]: ...
    def render(self, case: CaseInput) -> str: ...
    def parse(self, raw: str) -> dict[str, Any]: ...
    def score(
        self, parsed: dict[str, Any], case: CaseInput
    ) -> tuple[float, dict[str, float]]: ...


# ---------------------------------------------------------------------------
# Flat-YAML parser for expected.yaml (stdlib-only; no pyyaml dependency)
# ---------------------------------------------------------------------------


def _split_top(s: str) -> list[str]:
    """Split *s* on commas that are not inside quotes or nested brackets."""
    parts: list[str] = []
    cur: list[str] = []
    in_q: str | None = None
    depth = 0
    for ch in s:
        if in_q:
            cur.append(ch)
            if ch == in_q:
                in_q = None
        elif ch in "\"'":
            in_q = ch
            cur.append(ch)
        elif ch == "[":
            depth += 1
            cur.append(ch)
        elif ch == "]":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _parse_scalar(raw: str) -> Any:
    """Parse one YAML scalar or inline list (recursively)."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in _split_top(inner)]
    s = raw.strip().strip("\"'")
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


def parse_flat_yaml(text: str) -> dict[str, Any]:
    """Parse the flat ``key: value`` YAML used by ``*.expected.yaml``.

    Handles scalars (int/bool/float/str) and inline ``[a, b, "c"]`` lists. No
    nested mappings — the eval harness's expected-characteristics files are flat
    by design. Kept stdlib-only so this module imports under ``make test``.
    """
    out: dict[str, Any] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not key or not raw:
            continue
        out[key] = _parse_scalar(raw)
    return out


# ---------------------------------------------------------------------------
# Base class — shared case-discovery + fixture loading
# ---------------------------------------------------------------------------


class BaseEvaluator:
    """Shared golden-case discovery; subclasses supply the prompt-specific bits.

    Cases are discovered from ``*.expected.yaml`` files in the prompt's fixture
    subdir; each case's render-variable inputs are loaded by
    :meth:`_load_inputs` (subclass), which returns ``None`` to skip a case whose
    required input file is missing.
    """

    prompt_id: str

    def _fixture_dir(self) -> Path:
        return _FIXTURE_ROOT / self.prompt_id

    def _load_expected(self, stem: str) -> dict[str, Any] | None:
        path = self._fixture_dir() / f"{stem}.expected.yaml"
        if not path.is_file():
            return None
        return parse_flat_yaml(path.read_text(encoding="utf-8"))

    def _load_inputs(self, stem: str) -> dict[str, str] | None:
        """Return the render variables loaded from this case's input fixture(s).

        Returning ``None`` skips the case (a required input file is absent).
        """
        raise NotImplementedError

    def _input_text(self, stem: str, name: str) -> str | None:
        """Read ``<stem>.<name>.md`` from the fixture dir, or None if absent."""
        path = self._fixture_dir() / f"{stem}.{name}.md"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def load_cases(self, limit: int | None = None) -> list[CaseInput]:
        cases: list[CaseInput] = []
        expected_paths = sorted(self._fixture_dir().glob("*.expected.yaml"))
        for path in expected_paths:
            stem = path.name[: -len(".expected.yaml")]
            expected = self._load_expected(stem)
            if expected is None:
                continue
            inputs = self._load_inputs(stem)
            if inputs is None:
                continue
            cases.append(CaseInput(case_id=stem, inputs=inputs, expected=expected))
        if limit:
            cases = cases[:limit]
        return cases

    # render / parse / score are implemented by each subclass.

    @property
    def version_stamp(self) -> str:
        """The ``<id>@<version>`` stamp for cache keying."""
        import prompt_templates  # local: keeps the module import-light

        tpl = prompt_templates.load_prompt(self.prompt_id)
        return f"{tpl.id}@{tpl.version}"
