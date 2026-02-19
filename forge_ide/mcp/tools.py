"""MCP tool definitions and dispatch logic."""

from __future__ import annotations

from typing import Any

from .artifact_store import (
    clear_artifacts,
    get_artifact,
    list_artifacts,
    store_artifact,
)
from .config import LOCAL_MODE
from .local import get_invariants, get_summary, list_contracts, load_contract
from .remote import api_get

# ── Tool catalogue ────────────────────────────────────────────────────────

# Artifact store tools are always served in-process (not proxied to API)
_ARTIFACT_TOOLS = frozenset(
    {"forge_store_artifact", "forge_get_artifact", "forge_list_artifacts", "forge_clear_artifacts"}
)

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
    # ── Artifact store (MCP temp pattern) ─────────────────────────────────
    {
        "name": "forge_store_artifact",
        "description": (
            "Store a generated artifact (contract, scout dossier, renovation "
            "plan, builder directive, phase output, etc.) in the MCP artifact "
            "store for on-demand retrieval by other sub-agents.  Use this "
            "instead of embedding large content in agent system prompts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project identifier",
                },
                "artifact_type": {
                    "type": "string",
                    "description": (
                        "Category: contract | scout | renovation | "
                        "directive | phase | seal | diff"
                    ),
                },
                "key": {
                    "type": "string",
                    "description": (
                        "Artifact key within its type, e.g. 'manifesto', "
                        "'dossier', 'stack_profile', 'executive_brief'"
                    ),
                },
                "content": {
                    "description": "Artifact content (string or JSON-serialisable value)",
                },
                "ttl_hours": {
                    "type": "number",
                    "description": "Memory TTL in hours (default: 24).  Disk copies are permanent.",
                },
                "persist": {
                    "type": "boolean",
                    "description": "Write to disk for cross-session durability (default: true)",
                },
            },
            "required": ["project_id", "artifact_type", "key", "content"],
        },
    },
    {
        "name": "forge_get_artifact",
        "description": (
            "Retrieve a previously stored artifact by project ID, type, and "
            "key.  Checks in-memory store first, then disk.  Use this to "
            "lazy-load contracts or other large context on demand."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "contract | scout | renovation | directive | phase | seal | diff",
                },
                "key": {
                    "type": "string",
                    "description": "Artifact key, e.g. 'manifesto', 'dossier'",
                },
            },
            "required": ["project_id", "artifact_type", "key"],
        },
    },
    {
        "name": "forge_list_artifacts",
        "description": (
            "List all stored artifacts for a project, optionally filtered "
            "by type.  Shows key, source (memory/disk), age, and size."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "Optional filter: contract | scout | renovation | directive | phase | seal | diff",
                },
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "forge_clear_artifacts",
        "description": (
            "Remove all stored artifacts for a project (memory + disk), "
            "optionally scoped to a single artifact type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "artifact_type": {
                    "type": "string",
                    "description": "Optional — clear only this type",
                },
            },
            "required": ["project_id"],
        },
    },
]


# ── Dispatch ──────────────────────────────────────────────────────────────


async def dispatch(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route tool calls — artifact store (always in-process), then local disk or remote API."""
    # Artifact store tools are always served in-process regardless of LOCAL_MODE
    if name in _ARTIFACT_TOOLS:
        return _dispatch_artifact(name, arguments)
    if LOCAL_MODE:
        return _dispatch_local(name, arguments)
    return await _dispatch_remote(name, arguments)


def _dispatch_artifact(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Serve artifact store tool calls from in-process store."""
    project_id = arguments.get("project_id")
    artifact_type = arguments.get("artifact_type")
    key = arguments.get("key")

    match name:
        case "forge_store_artifact":
            if not all([project_id, artifact_type, key]):
                return {"error": "Missing required parameters: project_id, artifact_type, key"}
            content = arguments.get("content")
            ttl_hours = float(arguments.get("ttl_hours", 24))
            persist = bool(arguments.get("persist", True))
            return store_artifact(project_id, artifact_type, key, content, ttl_hours, persist)

        case "forge_get_artifact":
            if not all([project_id, artifact_type, key]):
                return {"error": "Missing required parameters: project_id, artifact_type, key"}
            return get_artifact(project_id, artifact_type, key)

        case "forge_list_artifacts":
            if not project_id:
                return {"error": "Missing required parameter: project_id"}
            return list_artifacts(project_id, artifact_type)

        case "forge_clear_artifacts":
            if not project_id:
                return {"error": "Missing required parameter: project_id"}
            return clear_artifacts(project_id, artifact_type)

        case _:
            return {"error": f"Unknown artifact tool: {name}"}


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
