"""Planning MCP — interactive plan review and browser annotation."""

from planning_mcp.tools import mcp


def main() -> None:
    """stdio entry point for Claude Code MCP integration."""
    mcp.run()
