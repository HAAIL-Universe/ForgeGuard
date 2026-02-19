"""Project-scoped MCP tool handlers — bridge MCP to ForgeGuard DB contracts.

These handlers call the ForgeGuard API ``/api/mcp/*`` endpoints to serve
per-project contracts stored in PostgreSQL.  They are always proxied
(never served from local disk) because project contracts only exist in
the database.

Session defaults (project_id, build_id) are resolved automatically via
``forge_ide.mcp.session``.
"""

from __future__ import annotations

from typing import Any

from .remote import api_get
from .session import resolve_build_id, resolve_project_id


async def get_project_context(arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch combined project manifest — metadata only, no full content.

    Endpoint: GET /api/mcp/context/{project_id}
    """
    project_id = resolve_project_id(arguments)
    if not project_id:
        return {"error": "Missing required parameter: project_id (not in arguments or session)"}
    return await api_get(f"/api/mcp/context/{project_id}")


async def list_project_contracts(arguments: dict[str, Any]) -> dict[str, Any]:
    """List all generated contracts for a project — types, versions, sizes.

    Endpoint: GET /api/mcp/context/{project_id}  (returns contract list)
    """
    project_id = resolve_project_id(arguments)
    if not project_id:
        return {"error": "Missing required parameter: project_id (not in arguments or session)"}
    resp = await api_get(f"/api/mcp/context/{project_id}")
    if "error" in resp:
        return resp
    # Extract just the contract listing from the full manifest
    return {
        "project_id": project_id,
        "items": resp.get("contracts", []),
        "count": len(resp.get("contracts", [])),
    }


async def get_project_contract(arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch a single generated contract for the current project.

    Endpoint: GET /api/mcp/context/{project_id}/{contract_type}
    """
    project_id = resolve_project_id(arguments)
    contract_type = arguments.get("contract_type")
    if not project_id:
        return {"error": "Missing required parameter: project_id (not in arguments or session)"}
    if not contract_type:
        return {"error": "Missing required parameter: contract_type"}
    return await api_get(f"/api/mcp/context/{project_id}/{contract_type}")


async def get_build_contracts(arguments: dict[str, Any]) -> dict[str, Any]:
    """Fetch the pinned contract snapshot for a specific build.

    Endpoint: GET /api/mcp/build/{build_id}/contracts
    """
    build_id = resolve_build_id(arguments)
    if not build_id:
        return {"error": "Missing required parameter: build_id (not in arguments or session)"}
    return await api_get(f"/api/mcp/build/{build_id}/contracts")
