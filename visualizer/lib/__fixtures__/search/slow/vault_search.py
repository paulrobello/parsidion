#!/usr/bin/env python3
"""Fake vault_search.py — sleeps to exercise timeout/concurrency (test fixture)."""

import json
import time

time.sleep(1.5)
print(json.dumps([]))
