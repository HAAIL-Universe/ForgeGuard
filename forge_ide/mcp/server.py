"""MCP Server wiring — list_tools, call_tool, and stdio entry point."""

from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .config import LOCAL_MODE, FORGEGUARD_URL
from .tools import TOOL_DEFINITIONS, dispatch

# ── Server instance ───────────────────────────────────────────────────────

server = Server("forgeguard")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Declare all available tools."""
    return [Tool(**defn) for defn in TOOL_DEFINITIONS]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool invocations by proxying to ForgeGuard API or reading locally."""
    try:
        result = await dispatch(name, arguments)
        text = json.dumps(result, indent=2, default=str)
    except Exception as exc:
        if not LOCAL_MODE:
            import httpx

            if isinstance(exc, httpx.HTTPStatusError):
                text = json.dumps({
                    "error": f"ForgeGuard API returned {exc.response.status_code}",
                    "detail": exc.response.text[:500],
                })
            elif isinstance(exc, httpx.ConnectError):
                text = json.dumps({
                    "error": "Cannot connect to ForgeGuard API",
                    "url": FORGEGUARD_URL,
                    "hint": "Ensure the server is running and FORGEGUARD_URL is correct.",
                })
            else:
                text = json.dumps({"error": str(exc)})
        else:
            text = json.dumps({"error": str(exc)})

    return [TextContent(type="text", text=text)]


# ── Entry point ───────────────────────────────────────────────────────────


async def main():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
