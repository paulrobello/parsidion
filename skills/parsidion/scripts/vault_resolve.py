#!/usr/bin/env python3
"""vault_resolve.py -- canonical vault resolver CLI for non-Python callers.

ENH-009: the visualizer (and any other non-Python client) resolves vaults by
shelling out to this script instead of reimplementing the allowlist in
TypeScript. It is a thin wrapper over
:func:`core.vault_path.resolve_vault_server`, so the resolution algorithm has
exactly one implementation (was QA-012 / ARC-007 / SEC-P001).

Usage::

    vault_resolve.py [NAME]   # resolved vault path for NAME (or the default)
    vault_resolve.py --list    # {"default","named":[{"name","path"},...]} JSON

Exit codes:
    0  success (path on stdout, or JSON for ``--list``)
    1  the reference is neither a named vault nor the default (VaultConfigError)
    2  usage error

Stdlib-only (hook/script constraint). Run via ``uv run --no-project``.
"""

from __future__ import annotations

import argparse
import json
import sys

# This script lives at scripts/vault_resolve.py and core/ is a sibling
# subpackage (scripts/core/), so ``core.vault_path`` resolves off the script's
# own directory without any install step.
from core.vault_path import VaultConfigError, list_named_vaults, resolve_vault_server


def _emit_list() -> int:
    named = list_named_vaults()
    payload = {
        "default": str(resolve_vault_server(None)),
        "named": [{"name": name, "path": str(path)} for name, path in named.items()],
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Resolve a vault name/path to its absolute path and print it as JSON."""
    parser = argparse.ArgumentParser(
        prog="vault_resolve.py",
        description="Resolve a Parsidion vault name/path to its absolute path.",
    )
    parser.add_argument(
        "name",
        nargs="?",
        help="Vault name (from vaults.yaml) or path. Omit to resolve the default vault.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help='Emit {"default","named":[...]} JSON and exit.',
    )
    args = parser.parse_args(argv)

    try:
        if args.list:
            return _emit_list()
        sys.stdout.write(str(resolve_vault_server(args.name)) + "\n")
        return 0
    except VaultConfigError as exc:
        # Exit 1 is the contract for "not a known vault" so the TS caller can
        # map it to its VaultConfigError (HTTP 400). Unexpected errors use a
        # distinct code (2) so they surface as server errors, not client errors.
        sys.stderr.write(str(exc) + "\n")
        return 1
    except Exception:  # noqa: BLE001 — pragma: no cover; surface as a server error (exit 2), not a 400
        import traceback

        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
