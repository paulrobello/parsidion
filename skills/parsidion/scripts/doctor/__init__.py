"""vault_doctor package — scan vault notes for issues; optionally repair.

This package decomposes the original 3,128-line ``vault_doctor.py`` God module
(QA-003 / ARC-008) into focused submodules behind a ``Fixer`` protocol.  The
``vault_doctor.py`` script remains as a thin re-export shim so existing imports
(``import vault_doctor``; ``uv run --no-project vault_doctor.py …``) keep
working byte-for-byte.

Public surface (re-exported from the shim):

- ``Issue`` dataclass, ``VALID_TYPES`` and other constants — ``doctor._state``.
- Per-concern scan/fix functions — ``doctor.{links,check,frontmatter,tags,
  headings,subfolder,prefixes,daily,sessions,permissions,graph}``.
- ``run_scan_and_repair`` orchestrator — ``doctor.orchestrator``.
- ``main`` CLI entry point — ``doctor.cli``.

Stdlib-only — same constraint as the original script.
"""
