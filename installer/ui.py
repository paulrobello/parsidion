"""Interactive UI helpers for the Parsidion installer.

Contains print helpers, prompts, and step/warning/error/ok functions.
Stdlib-only — no third-party dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path

from installer.colors import bold, cyan, dim, green, red, yellow
from installer.paths import validate_vault_path

# Re-exported so callers can treat ``installer.ui`` as the single UI facade
# (colour helpers + print/prompt helpers) rather than importing ``dim`` from
# ``installer.colors`` directly.
__all__ = [
    "bold",
    "cyan",
    "dim",
    "green",
    "prompt_vault_path",
    "red",
    "resolve_runtime_choice",
    "yellow",
]


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


# ---------------------------------------------------------------------------
# Prompt helpers
#
# ARC-002: these live here (next to ``_ask``) so tests patch the source
# binding — ``monkeypatch.setattr(installer.ui, "_ask", …)`` — rather than a
# re-export on ``install.py``'s namespace. ``install.py`` re-exports the
# function names for callers that still go through ``install.X``.
# ---------------------------------------------------------------------------


def prompt_vault_path(default: Path) -> Path:
    """Interactively prompt for the Obsidian vault path with validation."""
    print()
    print(bold("Obsidian Vault Location"))
    print(
        dim(
            "This is where Parsidion will store your knowledge notes.\n"
            "It can be an existing Obsidian vault or a new directory."
        )
    )
    while True:
        raw = _ask("Vault path", str(default))
        vault_path, error = validate_vault_path(raw)
        if error:
            _err(error)
            continue
        if vault_path.exists() and not vault_path.is_dir():
            _err(f"Path exists but is not a directory: {vault_path}")
            continue
        if not vault_path.exists():
            print(f"  {dim(str(vault_path))} does not exist.")
            if not _confirm("Create it?", default=True):
                continue
        return vault_path


def resolve_runtime_choice(
    runtime: str | None,
    *,
    yes: bool,
    interactive: bool,
) -> str:
    """Resolve runtime selection for install/uninstall flows."""
    if runtime:
        return runtime
    if yes or not interactive:
        return "claude"

    print()
    print(bold("Runtime Integrations"))
    print(
        dim(
            "  1. Claude only — ~/.claude settings, skills, agents, and hooks.\n"
            "  2. Codex only — ~/.codex hooks for SessionStart and Stop.\n"
            "  3. Gemini only — ~/.gemini settings hooks for SessionStart and SessionEnd.\n"
            "  4. Claude + Codex.\n"
            "  5. All runtimes — Claude + Codex + Gemini.\n"
            "  6. Shared tooling only — no runtime hooks."
        )
    )
    answer = _ask("Install runtime integrations", default="both").strip().lower()
    if answer in ("", "4", "both", "claude+codex", "claude + codex"):
        return "both"
    if answer in ("1", "claude", "claude only"):
        return "claude"
    if answer in ("2", "codex", "codex only"):
        return "codex"
    if answer in ("3", "gemini", "gemini only"):
        return "gemini"
    if answer in ("5", "all", "all runtimes", "claude+codex+gemini"):
        return "all"
    if answer in ("6", "none", "shared", "shared tooling only"):
        return "none"
    _warn(f"Unknown runtime selection {answer!r}; defaulting to both")
    return "both"
