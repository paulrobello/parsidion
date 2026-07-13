"""Smoke tests for server.py wiring."""

from parsidion_mcp.server import mcp


def test_mcp_instance_exists() -> None:
    assert mcp is not None
    assert mcp.name == "parsidion-mcp"


def test_all_tool_modules_importable() -> None:
    """Verify all tool functions are importable and callable.

    Avoids FastMCP private internals (_tool_manager) which may change
    between versions. Correct registration is implicitly verified: if
    server.py imports cleanly and mcp.tool()(fn) raised no error at
    module load time, all tools are registered.
    """
    from parsidion_mcp.tools.code_search import code_search
    from parsidion_mcp.tools.context import vault_context
    from parsidion_mcp.tools.notes import vault_read, vault_write
    from parsidion_mcp.tools.ops import rebuild_index, vault_doctor
    from parsidion_mcp.tools.search import vault_search

    for fn in [
        vault_search,
        vault_read,
        vault_write,
        vault_context,
        rebuild_index,
        vault_doctor,
        code_search,
    ]:
        assert callable(fn)
