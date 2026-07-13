#!/usr/bin/env python3
"""Fake vault_search.py — exits nonzero (test fixture)."""

import sys

sys.stderr.write("boom\n")
sys.exit(3)
