#!/usr/bin/env python3
"""Parsidion installer — thin entry shim (ARC-008).

The implementation lives in the ``installer`` package:
  installer.cli   — argument parsing, the install()/uninstall() flow driver,
                    and the ``connect``/``disconnect`` verb handling
  installer.plan  — the resolved install matrix (InstallPlan), the
                    interactive option prompts, the plan printer, and the
                    ordered StepList builder

Every historical invocation keeps working unchanged, e.g.::

    uv run install.py --force --yes
    uv run install.py connect codex
    uv run install.py --schedule-summarizer --summarizer-hour 3
    python install.py --dry-run

Tests that previously patched ``install.<name>`` now patch the owning
module (``installer.plan.install_skill``, ``installer.cli.install``, ...).
"""

from installer.cli import main

if __name__ == "__main__":
    main()
