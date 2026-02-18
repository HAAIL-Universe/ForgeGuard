"""MCP package entry point — allows ``python -m forge_ide.mcp``."""

import asyncio

from .server import main

if __name__ == "__main__":
    asyncio.run(main())
