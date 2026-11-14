"""The parsidion wheel namespace (ARC-201).

Everything under this package ships inside the ``parsidion.*`` namespace so
site-packages never receives generic top-level names (``core``, ``cli``,
``vault_search``, ...). The by-path entry scripts (hooks, ``update_index``,
``build_graph``, ...) intentionally stay at the ``scripts/`` root: they are
invoked by file path from hook registrations and Makefile targets, not
imported from an installed wheel, and several carry test monkeypatch
surfaces that depend on their root module names.
"""
