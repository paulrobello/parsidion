#!/usr/bin/env python3
"""Fake vault_search.py — sleeps to exercise timeout/concurrency (test fixture).

QA-019: the sleep was hard-coded to 1.5 s — over 20% of the bun test wall
time. Make it injectable via $VAULT_SEARCH_SLOW_DELAY_S (seconds) so a
loaded CI can extend it without editing the fixture, and lower the default
to 0.5 s (still well above the 250 ms timeout the test asserts, the 100 ms
abort window, and the concurrency-slot takeover delay — but 3x faster than
the previous default).
"""

import json
import os
import time

# Default chosen so that the slow path stays in flight long enough for the
# 250 ms timeout test, the 100 ms abort test, and the two-concurrent-search
# takeover window — but no longer. Override via env var when a CI runner
# is too loaded for the default to be reliable.
time.sleep(float(os.environ.get("VAULT_SEARCH_SLOW_DELAY_S", "0.5")))
print(json.dumps([]))
