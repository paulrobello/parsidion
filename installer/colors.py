"""ANSI colour helpers for the Parsidion installer.

Disabled automatically when stdout is not a TTY or NO_COLOR is set.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import os
import sys

_USE_COLOUR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def _colorize(code: str, text: str) -> str:
    """Wrap *text* in ANSI escape *code*, respecting NO_COLOR and non-TTY output."""
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text


def bold(t: str) -> str:
    """Return *t* rendered in bold."""
    return _colorize("1", t)


def green(t: str) -> str:
    """Return *t* in bright green."""
    return _colorize("92", t)


def yellow(t: str) -> str:
    """Return *t* in bright yellow."""
    return _colorize("93", t)


def red(t: str) -> str:
    """Return *t* in bright red."""
    return _colorize("91", t)


def cyan(t: str) -> str:
    """Return *t* in bright cyan."""
    return _colorize("96", t)


def dim(t: str) -> str:
    """Return *t* in dim (faint) style."""
    return _colorize("2", t)
