"""Builds router -- endpoints for build orchestration lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.api.rate_limit import build_limiter
from app.services import build_service

router = APIRouter(prefix="/projects", tags=["builds"])


class StartBuildRequest(BaseModel):
    """Request body for starting a build."""
    target_type: str | None = None
    target_ref: str | None = None
    branch: str = "main"
    contract_batch: int | None = None


class DeleteBuildsRequest(BaseModel):
    """Request body for deleting selected builds."""
    build_ids: list[str]


class ResumeRequest(BaseModel):
    """Request body for resuming a paused build."""
    action: str = "retry"  # "retry" | "skip" | "abort" | "edit"


class InterjectRequest(BaseModel):
    """Request body for injecting a user message into an active build."""
    message: str


# ── POST /projects/{project_id}/build ────────────────────────────────────


@router.post("/{project_id}/build")
async def start_build(
    project_id: UUID,
    body: StartBuildRequest | None = None,
    user: dict = Depends(get_current_user),
):
    """Start a build for a project."""
    if not build_limiter.is_allowed(str(user["id"])):
        raise HTTPException(status_code=429, detail="Build rate limit exceeded")
    try:
        build = await build_service.start_build(
            project_id,
            user["id"],
            target_type=body.target_type if body else None,
            target_ref=body.target_ref if body else None,
            branch=body.branch if body else "main",
            contract_batch=body.contract_batch if body else None,
        )
        return build
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/builds ─────────────────────────────────────


@router.get("/{project_id}/builds")
async def list_builds(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """List all builds for a project, newest first."""
    try:
        builds = await build_service.list_builds(project_id, user["id"])
        return {"items": builds}
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── DELETE /projects/{project_id}/builds ──────────────────────────────────


@router.delete("/{project_id}/builds")
async def delete_builds(
    project_id: UUID,
    body: DeleteBuildsRequest,
    user: dict = Depends(get_current_user),
):
    """Delete selected builds for a project."""
    try:
        deleted = await build_service.delete_builds(
            project_id, user["id"], body.build_ids
        )
        return {"deleted": deleted}
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


# ── POST /projects/{project_id}/build/force-cancel ───────────────────────


@router.post("/{project_id}/build/force-cancel")
async def force_cancel_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Force-cancel a stuck build (manual recovery)."""
    try:
        build = await build_service.force_cancel_build(project_id, user["id"])
        return build
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── POST /projects/{project_id}/build/resume ─────────────────────────────


@router.post("/{project_id}/build/resume")
async def resume_build(
    project_id: UUID,
    body: ResumeRequest,
    user: dict = Depends(get_current_user),
):
    """Resume a paused build with the chosen action."""
    try:
        build = await build_service.resume_build(
            project_id, user["id"], action=body.action
        )
        return build
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── POST /projects/{project_id}/build/interject ──────────────────────────


@router.post("/{project_id}/build/interject")
async def interject_build(
    project_id: UUID,
    body: InterjectRequest,
    user: dict = Depends(get_current_user),
):
    """Inject a user message into an active build."""
    try:
        return await build_service.interject_build(
            project_id, user["id"], body.message
        )
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception(
            "Unhandled error in interject_build for project %s", project_id
        )
        raise HTTPException(status_code=500, detail=str(exc))


# ── GET /projects/{project_id}/build/files ────────────────────────────────


@router.get("/{project_id}/build/files")
async def get_build_files(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """List all files written during the build."""
    try:
        files = await build_service.get_build_files(project_id, user["id"])
        return {"items": files}
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/files/{path} ─────────────────────────


@router.get("/{project_id}/build/files/{path:path}")
async def get_build_file_content(
    project_id: UUID,
    path: str,
    user: dict = Depends(get_current_user),
):
    """Retrieve content of a specific file written during the build."""
    try:
        return await build_service.get_build_file_content(
            project_id, user["id"], path
        )
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
    search: str | None = Query(default=None),
    level: str | None = Query(default=None),
):
    """Get paginated build logs with optional search and level filter."""
    try:
        logs, total = await build_service.get_build_logs(
            project_id, user["id"], limit, offset,
            search=search, level=level,
        )
        return {"items": logs, "total": total}
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/phases ──────────────────────────────


@router.get("/{project_id}/build/phases")
async def get_build_phases(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Phase definitions parsed from the project's phases contract."""
    try:
        return await build_service.get_build_phases(project_id, user["id"])
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/summary ─────────────────────────────


@router.get("/{project_id}/build/summary")
async def get_build_summary(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Complete build summary with cost breakdown."""
    try:
        return await build_service.get_build_summary(project_id, user["id"])
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/instructions ────────────────────────


@router.get("/{project_id}/build/instructions")
async def get_build_instructions(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Generated deployment instructions."""
    try:
        return await build_service.get_build_instructions(project_id, user["id"])
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── POST /projects/{project_id}/build/circuit-break ──────────────────────


@router.post("/{project_id}/build/circuit-break")
async def circuit_break_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Circuit breaker — immediate hard stop.  Kills the build and logs
    the reason as CIRCUIT_BREAKER so it's distinguishable from normal
    cancellation."""
    try:
        build = await build_service.force_cancel_build(project_id, user["id"])
        # Log the specific reason
        from app.repos import build_repo as _br
        await _br.append_build_log(
            build["id"],
            "CIRCUIT BREAKER activated by user — all API calls halted",
            source="system", level="error",
        )
        return {**build, "circuit_breaker": True}
    except ValueError as exc:
        detail = str(exc)
        if "not found" in detail.lower():
            raise HTTPException(status_code=404, detail=detail)
        raise HTTPException(status_code=400, detail=detail)


# ── GET /projects/{project_id}/build/live-cost ───────────────────────────


@router.get("/{project_id}/build/live-cost")
async def get_live_cost(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Return current in-memory cost data for the active build."""
    from app.repos import build_repo as _br
    latest = await _br.get_latest_build_for_project(project_id)
    if not latest:
        raise HTTPException(status_code=404, detail="No builds found")
    bid = str(latest["id"])
    return build_service.get_build_cost_live(bid)
