"""parsidion-mcp: FastMCP server exposing Parsidion vault to Claude Desktop."""

from fastmcp import FastMCP

from parsidion_mcp.tools.context import vault_context
from parsidion_mcp.tools.notes import vault_read, vault_write
from parsidion_mcp.tools.ops import rebuild_index, vault_doctor
from parsidion_mcp.tools.search import vault_search

mcp = FastMCP("parsidion-mcp")

mcp.tool()(vault_search)
mcp.tool()(vault_read)
mcp.tool()(vault_write)
mcp.tool()(vault_context)
mcp.tool()(rebuild_index)
mcp.tool()(vault_doctor)


def main() -> None:
    """Entry point for the ``parsidion-mcp`` command."""
    mcp.run()
