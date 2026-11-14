"""``core`` — stdlib-only library package for the Parsidion vault tooling (ARC-004).

Holds the shared library implementations previously flat in ``scripts/``:
config, path, filesystem, index, hooks, adaptive-context, links, constants,
metrics, and the AI / parsight / subprocess backends.

Hard constraint — every module here is **Python stdlib only**.  The constraint
is enforced structurally by ``tests/test_stdlib_only.py``, which imports each
``core/*`` module (and every hook) in a fresh interpreter with ``sys.modules``
poisoned against ``rich`` / ``fastembed`` / ``sqlite_vec`` / ``anyio`` / ``yaml``
/ ``numpy`` / ``PIL`` so a forbidden import — even a transitive one — fails the
gate.  The original flat module names (``vault_config``, ``vault_path``, …)
remain importable as thin re-export shims in the parent ``scripts/`` directory.
"""
