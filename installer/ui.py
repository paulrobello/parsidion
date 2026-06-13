"""Interactive UI helpers for the Parsidion installer.

Contains print helpers, prompts, and step/warning/error/ok functions.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import sys

from installer.colors import cyan, dim, green, red, yellow

# Re-exported so callers can treat ``installer.ui`` as the single UI facade
# (colour helpers + print/prompt helpers) rather than importing ``dim`` from
# ``installer.colors`` directly.
__all__ = ["cyan", "dim", "green", "red", "yellow"]


def _print(msg: str, verbose_only: bool = False, verbose: bool = False) -> None:
    """Print *msg*, optionally gating on the *verbose* flag.

    Args:
        msg: The message to print.
        verbose_only: When True, suppress output unless *verbose* is also True.
        verbose: Whether verbose output is enabled (passed through from the CLI flag).
    """
    if verbose_only and not verbose:
        return
    print(msg)


def _make_vprint(verbose: bool):
    """Return a ``vprint(msg)`` closure bound to *verbose*.

    Use this inside functions that receive the ``verbose`` flag to avoid
    passing it at every ``_print`` call site::

        vprint = _make_vprint(verbose)
        vprint("debug info")          # only printed when verbose=True
        vprint("always shown", always=True)

    Args:
        verbose: The global verbosity flag.

    Returns:
        A callable ``vprint(msg, always=False)`` that prints *msg* when
        *verbose* is True, or always when *always* is True.
    """

    def vprint(msg: str, always: bool = False) -> None:
        """Print *msg* when verbose mode is active or *always* is True."""
        if always or verbose:
            print(msg)

    return vprint


def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input, returning *default* on empty reply."""
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{cyan('?')} {prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    return answer if answer else default


def _confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question; return True for yes."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"{cyan('?')} {prompt} [{hint}]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)
    if not answer:
        return default
    return answer in ("y", "yes")


def _step(label: str, dry_run: bool = False) -> None:
    """Print an installation step with a green '+' prefix, or '[dry-run]' when previewing."""
    prefix = yellow("[dry-run]") if dry_run else green("  +")
    print(f"{prefix} {label}")


def _warn(msg: str) -> None:
    """Print a yellow warning message to stdout."""
    print(f"{yellow('  !')} {msg}")


def _err(msg: str) -> None:
    """Print a red error message to stderr."""
    print(f"{red('  ✗')} {msg}", file=sys.stderr)


def _ok(msg: str) -> None:
    """Print a green success message to stdout."""
    print(f"{green('  ✓')} {msg}")
