"""Builds router -- endpoints for build orchestration lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.services import build_service

router = APIRouter(prefix="/projects", tags=["builds"])


# ── POST /projects/{project_id}/build ────────────────────────────────────


@router.post("/{project_id}/build")
async def start_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Start a build for a project."""
    try:
        build = await build_service.start_build(project_id, user["id"])
        return build
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── POST /projects/{project_id}/build/cancel ─────────────────────────────


@router.post("/{project_id}/build/cancel")
async def cancel_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Cancel an active build."""
    try:
        build = await build_service.cancel_build(project_id, user["id"])
        return build
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/status ──────────────────────────────


@router.get("/{project_id}/build/status")
async def get_build_status(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Get current build status."""
    try:
        return await build_service.get_build_status(project_id, user["id"])
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/logs ────────────────────────────────


@router.get("/{project_id}/build/logs")
async def get_build_logs(
    project_id: UUID,
    user: dict = Depends(get_current_user),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    """Get paginated build logs."""
    try:
        logs, total = await build_service.get_build_logs(
            project_id, user["id"], limit, offset
        )
        return {"items": logs, "total": total}
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
