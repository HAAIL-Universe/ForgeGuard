"""MCP tool definitions and dispatch logic."""

from __future__ import annotations

from typing import Any

from .config import LOCAL_MODE
from .local import get_invariants, get_summary, list_contracts, load_contract
from .remote import api_get

# ── Tool catalogue ────────────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "forge_summary",
        "description": (
            "Get a compact overview of the entire ForgeGuard governance "
            "framework — all contracts, invariants, layer boundaries, and "
            "available endpoints. Call this FIRST to understand the framework."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_list_contracts",
        "description": (
            "List all available governance contracts with their names, "
            "filenames, and formats (json/yaml/markdown)."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_contract",
        "description": (
            "Read the full content of a specific governance contract. "
            "Use forge_list_contracts first to see available names. "
            "Key contracts: boundaries (architecture rules), physics "
            "(API spec), builder_directive (build rules), stack (tech stack)."
        ),
        "inputSchema": {
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
    },
    {
        "name": "forge_get_invariants",
        "description": (
            "Get all invariant gate definitions — hard constraints enforced "
            "during builds. Includes constraint types (MONOTONIC_UP, EQUAL, "
            "etc.) and default values."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_boundaries",
        "description": (
            "Get architectural layer boundary rules — which imports and "
            "patterns are forbidden in each layer (routers, repos, clients, "
            "audit_engine, services). Violation of these rules fails the build."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_physics",
        "description": (
            "Get the canonical API specification (physics.yaml) — the "
            "single source of truth for every endpoint, auth type, request "
            "and response schemas."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_directive",
        "description": (
            "Get the builder directive — the system prompt and rules that "
            "govern how the AI builder operates during code generation."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forge_get_stack",
        "description": (
            "Get the technology stack contract — required languages, "
            "frameworks, versions, and dependencies."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


# ── Dispatch ──────────────────────────────────────────────────────────────


async def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route tool calls — local disk or remote API depending on mode."""
    if LOCAL_MODE:
        return _dispatch_local(name, arguments)
    return await _dispatch_remote(name, arguments)


def _dispatch_local(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Serve from local disk."""
    match name:
        case "forge_summary":
            return get_summary()
        case "forge_list_contracts":
            return list_contracts()
        case "forge_get_contract":
            contract_name = arguments.get("name")
            if not contract_name:
                return {"error": "Missing required parameter: name"}
            return load_contract(contract_name)
        case "forge_get_invariants":
            return get_invariants()
        case "forge_get_boundaries":
            return load_contract("boundaries")
        case "forge_get_physics":
            return load_contract("physics")
        case "forge_get_directive":
            return load_contract("builder_directive")
        case "forge_get_stack":
            return load_contract("stack")
        case _:
            return {"error": f"Unknown tool: {name}"}


async def _dispatch_remote(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Proxy to ForgeGuard API."""
    match name:
        case "forge_summary":
            return await api_get("/forge/summary")
        case "forge_list_contracts":
            return await api_get("/forge/contracts")
        case "forge_get_contract":
            contract_name = arguments.get("name")
            if not contract_name:
                return {"error": "Missing required parameter: name"}
            return await api_get(f"/forge/contracts/{contract_name}")
        case "forge_get_invariants":
            return await api_get("/forge/invariants")
        case "forge_get_boundaries":
            return await api_get("/forge/boundaries")
        case "forge_get_physics":
            return await api_get("/forge/physics")
        case "forge_get_directive":
            return await api_get("/forge/directive")
        case "forge_get_stack":
            return await api_get("/forge/stack")
        case _:
            return {"error": f"Unknown tool: {name}"}
