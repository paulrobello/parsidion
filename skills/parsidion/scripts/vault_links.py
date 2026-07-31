"""vault_links -- compatibility shim (ARC-004).

Implementation moved to ``core.vault_links``. This shim re-exports the
module's complete non-dunder surface so every existing caller --
``import vault_links``, ``from vault_links import X``, ``vault_links.X`` (hooks, CLIs,
tests, parsidion-mcp, the installer) -- keeps working unchanged,
including imported constants and test monkeypatch targets. The
stdlib-only constraint is enforced on ``core.vault_links`` by
``tests/test_stdlib_only.py``.
"""

from core.vault_links import (  # noqa: F401 -- full-surface re-export
    Callable,
    Path,
    _FENCE_RE,
    _FRONTMATTER_RE,
    _INLINE_CODE_RE,
    _RELATED_FIELD_RE,
    _iter_unprotected_spans,
    add_backlinks_to_existing,
    find_related_by_semantic,
    find_related_by_tags,
    inject_related_links,
    os,
    re,
    replace_wikilinks_outside_code,
    strip_unresolved_wikilinks,
    sub_wikilinks_outside_code,
    subprocess,
    vault_common,
)
