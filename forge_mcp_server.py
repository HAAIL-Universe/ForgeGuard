#!/usr/bin/env python3
"""ForgeGuard MCP Server — standalone Model Context Protocol server.

Exposes ForgeGuard governance contracts, invariants, and architectural
rules as MCP tools that Claude CLI (or any MCP-compatible client) can
call natively.

Usage
-----
    # stdio transport (default — for Claude CLI / Desktop)
    python forge_mcp_server.py

    # Or with environment variables:
    FORGEGUARD_URL=https://forgeguard.example.com \\
    FORGEGUARD_API_KEY=fg_xxxx \\
    python forge_mcp_server.py

Configuration (env vars or .env)
---------------------------------
    FORGEGUARD_URL       Base URL of the ForgeGuard API (default: http://localhost:8000)
    FORGEGUARD_API_KEY   Forge API key (fg_…) for authentication

Claude CLI config (~/.claude/settings.json or claude_desktop_config.json)
--------------------------------------------------------------------------
    {
      "mcpServers": {
        "forgeguard": {
          "command": "python",
          "args": ["/path/to/forge_mcp_server.py"],
          "env": {
            "FORGEGUARD_URL": "https://forgeguard.example.com",
            "FORGEGUARD_API_KEY": "fg_your_key_here"
          }
        }
      }
    }
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    print(
        "ERROR: The 'mcp' package is required.\n"
        "Install it with: pip install mcp\n",
        file=sys.stderr,
    )
    sys.exit(1)


# ── Configuration ─────────────────────────────────────────────────────────

FORGEGUARD_URL = os.getenv("FORGEGUARD_URL", "http://localhost:8000").rstrip("/")
FORGEGUARD_API_KEY = os.getenv("FORGEGUARD_API_KEY", "")

if not FORGEGUARD_API_KEY:
    print(
        "WARNING: FORGEGUARD_API_KEY not set. Requests will fail unless "
        "the server allows unauthenticated access.\n",
        file=sys.stderr,
    )


# ── HTTP client ───────────────────────────────────────────────────────────

_http_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        headers = {"User-Agent": "ForgeGuard-MCP/1.0"}
        if FORGEGUARD_API_KEY:
            headers["Authorization"] = f"Bearer {FORGEGUARD_API_KEY}"
        _http_client = httpx.AsyncClient(
            base_url=FORGEGUARD_URL,
            headers=headers,
            timeout=30.0,
        )
    return _http_client


async def _api_get(path: str) -> dict[str, Any]:
    """GET a ForgeGuard endpoint, returning parsed JSON."""
    client = _get_client()
    resp = await client.get(path)
    resp.raise_for_status()
    return resp.json()


# ── MCP Server ────────────────────────────────────────────────────────────

server = Server("forgeguard")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """Declare all available tools."""
    return [
        Tool(
            name="forge_summary",
            description=(
                "Get a compact overview of the entire ForgeGuard governance "
                "framework — all contracts, invariants, layer boundaries, and "
                "available endpoints. Call this FIRST to understand the framework."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_list_contracts",
            description=(
                "List all available governance contracts with their names, "
                "filenames, and formats (json/yaml/markdown)."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_get_contract",
            description=(
                "Read the full content of a specific governance contract. "
                "Use forge_list_contracts first to see available names. "
                "Key contracts: boundaries (architecture rules), physics "
                "(API spec), builder_directive (build rules), stack (tech stack)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Contract name — one of: boundaries, physics, "
                            "blueprint, builder_contract, builder_directive, "
                            "manifesto, phases, schema, stack, system_prompt, "
                            "ui, auditor_prompt, recovery_planner_prompt, "
                            "remediation, desktop_distribution_plan"
                        ),
                    },
                },
                "required": ["name"],
            },
        ),
        Tool(
            name="forge_get_invariants",
            description=(
                "Get all invariant gate definitions — hard constraints enforced "
                "during builds. Includes constraint types (MONOTONIC_UP, EQUAL, "
                "etc.) and default values."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_get_boundaries",
            description=(
                "Get architectural layer boundary rules — which imports and "
                "patterns are forbidden in each layer (routers, repos, clients, "
                "audit_engine, services). Violation of these rules fails the build."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_get_physics",
            description=(
                "Get the canonical API specification (physics.yaml) — the "
                "single source of truth for every endpoint, auth type, request "
                "and response schemas."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_get_directive",
            description=(
                "Get the builder directive — the system prompt and rules that "
                "govern how the AI builder operates during code generation."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="forge_get_stack",
            description=(
                "Get the technology stack contract — required languages, "
                "frameworks, versions, and dependencies."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Handle tool invocations by proxying to ForgeGuard API."""
    try:
        result = await _dispatch(name, arguments)
        text = json.dumps(result, indent=2, default=str)
    except httpx.HTTPStatusError as exc:
        text = json.dumps({
            "error": f"ForgeGuard API returned {exc.response.status_code}",
            "detail": exc.response.text[:500],
        })
    except httpx.ConnectError:
        text = json.dumps({
            "error": "Cannot connect to ForgeGuard API",
            "url": FORGEGUARD_URL,
            "hint": "Ensure the server is running and FORGEGUARD_URL is correct.",
        })
    except Exception as exc:
        text = json.dumps({"error": str(exc)})

    return [TextContent(type="text", text=text)]


async def _dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route tool calls to the correct API endpoint."""
    match name:
        case "forge_summary":
            return await _api_get("/forge/summary")
        case "forge_list_contracts":
            return await _api_get("/forge/contracts")
        case "forge_get_contract":
            contract_name = arguments.get("name")
            if not contract_name:
                return {"error": "Missing required parameter: name"}
            return await _api_get(f"/forge/contracts/{contract_name}")
        case "forge_get_invariants":
            return await _api_get("/forge/invariants")
        case "forge_get_boundaries":
            return await _api_get("/forge/boundaries")
        case "forge_get_physics":
            return await _api_get("/forge/physics")
        case "forge_get_directive":
            return await _api_get("/forge/directive")
        case "forge_get_stack":
            return await _api_get("/forge/stack")
        case _:
            return {"error": f"Unknown tool: {name}"}


# ── Entry point ───────────────────────────────────────────────────────────


async def main():
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
