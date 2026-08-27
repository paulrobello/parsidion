"""Transaction step-list primitives shared by ``install()`` and ``uninstall()``.

ARC-017 / QA-002: ``install.py:install()`` was a CC-67 monolith of ~12 bare
sequential ``_run_step`` calls with no rollback, and ``installer/uninstall.py
:uninstall()`` was its equally-complex inverse maintained as a separate
function — so the two drifted. This module provides the step abstraction that
both flows are rebuilt on top of:

* :class:`Step`  — one ordered, individually-testable step with a ``run()``
  (forward / install) action. QA-108 removed the never-wired ``undo()`` /
  ``on_undo`` rollback machinery: both ``install()`` and ``uninstall()``
  build forward-only step lists (uninstall is itself the inverse flow, and
  the vault is preserved across it by long-standing contract).
* :class:`StepList` — owns the ordered steps, the failed-step tracker
  (preserving ARC-022's "surface every failure, return non-zero" semantics),
  and the forward iteration.

The step *abstraction* lives here. The step *instances* — the actual
``on_run`` bodies — are still built in ``install.py`` (and in
``installer/uninstall.py``) because their lambdas reference install.py's
module-global function names (``install_skill``, ``merge_hooks``, ...) which
the test suite monkeypatches via ``install.<name>``. Moving the bodies into
this module would break that patch path, so only the infrastructure is here.

Composition with existing hardening (ARC-018 / SEC-105): ``hooks.merge_hooks``
already does its own per-RMW ``settings.json.bak`` snapshot + flock. The
transaction-level snapshot/restore lives inline in ``install()`` (snapshot
before the run, restore if any settings-mutating step failed) and composes
with, rather than duplicates, that per-write ``.bak``.

Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable, Iterator

from installer.ui import _err


# A step body takes no arguments (it closes over the resolved install ctx).
# Return value is ignored — many underlying helpers (install_skill, _confirm)
# return a Path/bool that the step doesn't use, so the alias permits any return.
StepBody = Callable[[], object]


@dataclass
class Step:
    """One ordered install step.

    Attributes:
        name:    Stable identifier surfaced in the failed-step summary and
                 in dry-run output (e.g. ``"merge_hooks"``). Matches the
                 function name tests monkeypatch via ``install.<name>``.
        on_run:  Forward action. Must raise on failure (the runner catches
                 and records it). Resolves referenced functions through the
                 *caller's* module globals so monkeypatch keeps working.
    """

    name: str
    on_run: StepBody

    def run(self) -> None:
        """Run this step's forward (install) action via ``on_run``."""
        self.on_run()


@dataclass
class StepList:
    """Ordered list of :class:`Step` with a forward runner.

    ``run_all()`` preserves ARC-022 semantics exactly: each step runs inside
    a ``try/except Exception`` (KeyboardInterrupt/SystemExit propagate so
    Ctrl-C still works mid-install); failures are appended to
    ``failed_steps`` and the run *continues* so a partial install surfaces
    as many independent failures as possible. The caller turns a non-empty
    ``failed_steps`` into a non-zero exit code.
    """

    steps: list[Step] = field(default_factory=list)
    failed_steps: list[tuple[str, BaseException]] = field(default_factory=list)

    def append(self, step: Step) -> StepList:
        """Append *step*; returns self for chaining."""
        self.steps.append(step)
        return self

    def extend(self, steps: list[Step]) -> StepList:
        """Append each step in *steps*; returns self for chaining."""
        self.steps.extend(steps)
        return self

    def __iter__(self) -> Iterator[Step]:
        return iter(self.steps)

    def __len__(self) -> int:
        return len(self.steps)

    def run_all(self) -> list[tuple[str, BaseException]]:
        """Run each step's ``run()`` in order; capture failures without
        aborting siblings. Returns ``failed_steps`` (empty on full success).
        """
        for step in self.steps:
            try:
                step.run()
            except Exception as exc:  # noqa: BLE001 — install must not crash
                self.failed_steps.append((step.name, exc))
                _err(f"Step '{step.name}' failed: {exc}")
        return self.failed_steps
