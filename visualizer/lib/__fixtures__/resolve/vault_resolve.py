#!/usr/bin/env python3
"""ARC-209 test stub for vault_resolve.py.

Counts invocations by appending to SPAWN_COUNTER (a tmp file the test
provides) and prints a fixed vault path, so tests can assert exactly how
many times the resolver subprocess ran without child_process mocking.
PARSIDION_STUB_FAIL=1 makes the stub exit 2 (a resolver failure that is
not the exit-1 "unknown vault" contract).
"""
import os
import pathlib
import sys

counter = pathlib.Path(os.environ["SPAWN_COUNTER"])
with counter.open("a", encoding="utf-8") as fh:
    fh.write("1")
if os.environ.get("PARSIDION_STUB_FAIL") == "1":
    sys.exit(2)
print("/vault/stub")
