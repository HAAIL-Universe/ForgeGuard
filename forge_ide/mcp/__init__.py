"""ForgeGuard MCP Server package.

Exposes ForgeGuard governance contracts, invariants, and architectural
rules as MCP tools that Claude CLI (or any MCP-compatible client) can
call natively.

Usage::

    # As a module:
    python -m forge_ide.mcp

    # Or import and run:
    from forge_ide.mcp import main
    asyncio.run(main())
"""

from .server import main  # noqa: F401

__all__ = ["main"]
