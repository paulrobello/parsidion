"""cli — parent package for the decomposed CLI tools (ARC-005).

Holds one subpackage per God-file CLI that was decomposed following the
``doctor/`` and ``summarizer/`` template:

- ``cli.stats``  — modes extracted from ``vault_stats.py``.
- ``cli.search`` — modes extracted from ``vault_search.py``.
- ``cli.merge``  — modes extracted from ``vault_merge.py``.
- ``cli.index``  — modes extracted from ``update_index.py``.

Each top-level ``vault_<tool>.py`` / ``update_index.py`` remains as a thin
re-export shim and CLI entry point (``[project.scripts]``), so the public
``vault-stats`` / ``vault-search`` / ``vault-merge`` / ``update_index``
commands and every ``import vault_<tool>`` consumer keep working unchanged.

The CLI scripts are stdlib-only at module load time (third-party imports
such as ``rich`` are lazy-imported inside functions, exactly as the
originals), mirroring the constraint the original God-files already followed.
"""
