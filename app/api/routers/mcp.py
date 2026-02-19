"""MCP context-broker endpoints — serve project-scoped contracts to MCP.

These thin endpoints wrap existing ``project_repo`` functions and are
consumed by the MCP server's project-scoped tools (``forge_get_project_contract``
etc.).  All endpoints require forge-key or JWT auth via ``get_forge_user``.

Routes
------
GET /api/mcp/context/{project_id}             — project manifest (metadata)
GET /api/mcp/context/{project_id}/{type}      — single contract content
GET /api/mcp/build/{build_id}/contracts       — pinned snapshot for a build
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.forge_auth import get_forge_user
from app.repos.build_repo import get_build_by_id, get_builds_for_project
from app.repos.project_repo import (
    get_contract_by_type,
    get_contracts_by_project,
    get_project_by_id,
    get_snapshot_batches,
    get_snapshot_contracts,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp", tags=["mcp"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _verify_project_access(
    project_id: UUID, user: dict
) -> dict:
    """Verify the authenticated user owns the project.  Returns project row."""
    project = await get_project_by_id(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if str(project["user_id"]) != str(user["user_id"]):
        raise HTTPException(status_code=403, detail="Not authorised for this project")
    return project


# ---------------------------------------------------------------------------
# GET /mcp/context/{project_id}  — project manifest (metadata only)
# ---------------------------------------------------------------------------


@router.get("/context/{project_id}")
async def get_project_context(
    project_id: UUID,
    user: dict = Depends(get_forge_user),
) -> dict:
    """Return a combined manifest: project info, contract list, build stats.

    Returns **metadata only** — no full contract content.  Sub-agents use
    this to decide which contracts to fetch.
    """
    project = await _verify_project_access(project_id, user)

    contracts = await get_contracts_by_project(project_id)
    batches = await get_snapshot_batches(project_id)
    builds = await get_builds_for_project(project_id)

    return {
        "project": {
            "id": str(project["id"]),
            "name": project.get("name", ""),
            "status": project.get("status", ""),
        },
        "contracts": [
            {
                "contract_type": c["contract_type"],
                "version": c["version"],
                "size_chars": len(c.get("content", "")),
                "updated_at": str(c["updated_at"]) if c.get("updated_at") else None,
            }
            for c in contracts
        ],
        "latest_batch": batches[0]["batch"] if batches else None,
        "build_count": len(builds),
    }


# ---------------------------------------------------------------------------
# GET /mcp/context/{project_id}/{contract_type}  — single contract
# ---------------------------------------------------------------------------


@router.get("/context/{project_id}/{contract_type}")
async def get_project_contract(
    project_id: UUID,
    contract_type: str,
    user: dict = Depends(get_forge_user),
) -> dict:
    """Return full content of a single generated contract."""
    await _verify_project_access(project_id, user)

    contract = await get_contract_by_type(project_id, contract_type)
    if not contract:
        raise HTTPException(
            status_code=404,
            detail=f"Contract '{contract_type}' not found for project",
        )
    return {
        "contract_type": contract["contract_type"],
        "content": contract["content"],
        "version": contract["version"],
        "project_id": str(project_id),
        "source": "project_db",
    }


# ---------------------------------------------------------------------------
# GET /mcp/build/{build_id}/contracts  — pinned snapshot
# ---------------------------------------------------------------------------


@router.get("/build/{build_id}/contracts")
async def get_build_contracts(
    build_id: UUID,
    user: dict = Depends(get_forge_user),
) -> dict:
    """Return the pinned contract snapshot for a build.

    Looks up the build's ``contract_batch``, then returns all contracts
    from that snapshot.  If no batch is pinned, returns an error.
    """
    build = await get_build_by_id(build_id)
    if not build:
        raise HTTPException(status_code=404, detail="Build not found")

    # Verify user owns the project this build belongs to
    await _verify_project_access(build["project_id"], user)

    batch = build.get("contract_batch")
    if batch is None:
        return {
            "build_id": str(build_id),
            "batch": None,
            "contracts": [],
            "detail": "No contract batch pinned for this build",
        }

    contracts = await get_snapshot_contracts(build["project_id"], batch)
    return {
        "build_id": str(build_id),
        "batch": batch,
        "pinned_at": str(build.get("started_at", "")),
        "contracts": [
            {
                "contract_type": c["contract_type"],
                "content": c["content"],
            }
            for c in contracts
        ],
    }
