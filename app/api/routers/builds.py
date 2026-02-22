"""Builds router -- endpoints for build orchestration lifecycle."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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
    fresh_start: bool = False


class DeleteBuildsRequest(BaseModel):
    """Request body for deleting selected builds."""
    build_ids: list[str]


class ResumeRequest(BaseModel):
    """Request body for resuming a paused build."""
    action: str = "retry"  # "retry" | "skip" | "abort" | "edit"


class InterjectRequest(BaseModel):
    """Request body for injecting a user message into an active build."""
    message: str


class ChatRequest(BaseModel):
    """Request body for build chat — free-text questions about the build."""
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict] | None = Field(
        default=None,
        description="Prior conversation turns [{role, content}] for multi-turn context.",
    )


class ClarifyRequest(BaseModel):
    """Request body for answering a builder clarification question."""
    question_id: str
    answer: str = Field(..., min_length=1, max_length=1000)


class ApprovePlanRequest(BaseModel):
    """Request body for approving or rejecting a build plan."""
    action: str = "approve"  # "approve" | "reject"


class CommenceBuildRequest(BaseModel):
    """Request body for commencing a build after IDE warm-up."""
    action: str = "commence"  # "commence" | "cancel"


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
        return await build_service.start_build(
            project_id,
            user["id"],
            target_type=body.target_type if body else None,
            target_ref=body.target_ref if body else None,
            branch=body.branch if body else "main",
            contract_batch=body.contract_batch if body else None,
            fresh_start=body.fresh_start if body else False,
        )
    except ValueError as exc:
        msg = str(exc).lower()
        if "not found" in msg:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── GET /projects/{project_id}/builds ─────────────────────────────────────


@router.get("/{project_id}/builds")
async def list_builds(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """List all builds for a project, newest first."""
    builds = await build_service.list_builds(project_id, user["id"])
    return {"items": builds}


# ── DELETE /projects/{project_id}/builds ──────────────────────────────────


@router.delete("/{project_id}/builds")
async def delete_builds(
    project_id: UUID,
    body: DeleteBuildsRequest,
    user: dict = Depends(get_current_user),
):
    """Delete selected builds for a project."""
    deleted = await build_service.delete_builds(
        project_id, user["id"], body.build_ids
    )
    return {"deleted": deleted}


# ── POST /projects/{project_id}/build/cancel ─────────────────────────────


@router.post("/{project_id}/build/cancel")
async def cancel_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Cancel an active build."""
    return await build_service.cancel_build(project_id, user["id"])


# ── POST /projects/{project_id}/build/force-cancel ───────────────────────


@router.post("/{project_id}/build/force-cancel")
async def force_cancel_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Force-cancel a stuck build (manual recovery)."""
    return await build_service.force_cancel_build(project_id, user["id"])


# ── POST /projects/{project_id}/build/nuke ────────────────────────────


@router.post("/{project_id}/build/nuke")
async def nuke_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Completely destroy a build — revert all git changes and delete the record.

    This is the nuclear option: force-cancels the build, deletes the remote
    branch (or force-pushes the default branch back to its pre-build state),
    and removes the build from the database.
    """
    try:
        return await build_service.nuke_build(project_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── POST /projects/{project_id}/build/resume ─────────────────────────────


@router.post("/{project_id}/build/resume")
async def resume_build(
    project_id: UUID,
    body: ResumeRequest,
    user: dict = Depends(get_current_user),
):
    """Resume a paused build with the chosen action."""
    return await build_service.resume_build(
        project_id, user["id"], action=body.action
    )


# ── POST /projects/{project_id}/build/interject ──────────────────────────


@router.post("/{project_id}/build/interject")
async def interject_build(
    project_id: UUID,
    body: InterjectRequest,
    user: dict = Depends(get_current_user),
):
    """Inject a user message into an active build."""
    return await build_service.interject_build(
        project_id, user["id"], body.message
    )


# ── POST /projects/{project_id}/build/chat ───────────────────────────────


@router.post("/{project_id}/build/chat")
async def build_chat(
    project_id: UUID,
    body: ChatRequest,
    user: dict = Depends(get_current_user),
):
    """Ask a free-text question about the build — answered by Haiku."""
    try:
        return await build_service.build_chat(
            project_id, user["id"], body.message, history=body.history
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── POST /projects/{project_id}/build/clarify ────────────────────────────


@router.post("/{project_id}/build/clarify")
async def clarify_build(
    project_id: UUID,
    body: ClarifyRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Submit the user's answer to a builder clarification question."""
    try:
        return await build_service.resume_clarification(
            project_id, user["id"], body.question_id, body.answer
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── POST /projects/{project_id}/build/approve-plan ──────────────────


@router.post("/{project_id}/build/approve-plan")
async def approve_plan(
    project_id: UUID,
    body: ApprovePlanRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Approve or reject a pending build plan.

    The build pauses after planning and waits for this call before
    spending tokens on Opus builders.
    """
    try:
        return await build_service.approve_plan(
            project_id, user["id"], action=body.action
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── POST /projects/{project_id}/build/commit-plan ───────────────────────


@router.post("/{project_id}/build/commit-plan")
async def commit_plan(
    project_id: UUID,
    user: dict = Depends(get_current_user),
) -> dict:
    """Commit the cached plan JSON to the workspace git repo."""
    try:
        return await build_service.commit_plan_to_git(project_id, user["id"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── POST /projects/{project_id}/build/commence ──────────────────────────


@router.post("/{project_id}/build/commence")
async def commence_build(
    project_id: UUID,
    body: CommenceBuildRequest,
    user: dict = Depends(get_current_user),
) -> dict:
    """Signal that the user is ready to start the build.

    Called after the IDE has warmed up and the ``forge_ide_ready`` event
    has been shown.  The build is paused until this endpoint is called.
    """
    try:
        return await build_service.commence_build(
            project_id, user["id"], action=body.action
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ── GET /projects/{project_id}/build/files ────────────────────────────────


@router.get("/{project_id}/build/files")
async def get_build_files(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """List all files written during the build."""
    files = await build_service.get_build_files(project_id, user["id"])
    return {"items": files}


# ── GET /projects/{project_id}/build/phase-files ──────────────────────────


@router.get("/{project_id}/build/phase-files")
async def get_build_phase_files(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Return files grouped by phase from stored phase outcomes."""
    data = await build_service.get_phase_files(project_id, user["id"])
    return data


# ── GET /projects/{project_id}/build/files/{path} ─────────────────────────


@router.get("/{project_id}/build/files/{path:path}")
async def get_build_file_content(
    project_id: UUID,
    path: str,
    user: dict = Depends(get_current_user),
):
    """Retrieve content of a specific file written during the build."""
    return await build_service.get_build_file_content(
        project_id, user["id"], path
    )


# ── GET /projects/{project_id}/build/status ──────────────────────────────


@router.get("/{project_id}/build/status")
async def get_build_status(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Get current build status."""
    return await build_service.get_build_status(project_id, user["id"])


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
    logs, total = await build_service.get_build_logs(
        project_id, user["id"], limit, offset,
        search=search, level=level,
    )
    return {"items": logs, "total": total}


# ── GET /projects/{project_id}/build/phases ──────────────────────────────


@router.get("/{project_id}/build/phases")
async def get_build_phases(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Phase definitions parsed from the project's phases contract."""
    return await build_service.get_build_phases(project_id, user["id"])


# ── GET /projects/{project_id}/build/summary ─────────────────────────────


@router.get("/{project_id}/build/summary")
async def get_build_summary(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Complete build summary with cost breakdown."""
    return await build_service.get_build_summary(project_id, user["id"])


# ── GET /projects/{project_id}/build/instructions ────────────────────────


@router.get("/{project_id}/build/instructions")
async def get_build_instructions(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Generated deployment instructions."""
    return await build_service.get_build_instructions(project_id, user["id"])


# ── POST /projects/{project_id}/build/circuit-break ──────────────────────


@router.post("/{project_id}/build/circuit-break")
async def circuit_break_build(
    project_id: UUID,
    user: dict = Depends(get_current_user),
):
    """Circuit breaker — immediate hard stop.  Kills the build and logs
    the reason as CIRCUIT_BREAKER so it's distinguishable from normal
    cancellation."""
    build = await build_service.force_cancel_build(project_id, user["id"])
    from app.repos import build_repo as _br
    await _br.append_build_log(
        build["id"],
        "CIRCUIT BREAKER activated by user — all API calls halted",
        source="system", level="error",
    )
    return {**build, "circuit_breaker": True}


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


# ── Build Errors ─────────────────────────────────────────────────────────


@router.get("/{project_id}/build/errors")
async def get_build_errors(
    project_id: UUID,
    resolved: bool | None = Query(None, description="Filter by resolved status"),
    user: dict = Depends(get_current_user),
):
    """Return aggregated build errors for the latest build."""
    from app.repos import build_repo as _br
    latest = await _br.get_latest_build_for_project(project_id)
    if not latest:
        raise HTTPException(status_code=404, detail="No builds found")
    errors = await _br.get_build_errors(
        latest["id"], resolved_filter=resolved,
    )
    # Serialize UUIDs + datetimes for JSON
    for e in errors:
        for k, v in e.items():
            if isinstance(v, UUID):
                e[k] = str(v)
            elif hasattr(v, "isoformat"):
                e[k] = v.isoformat()
    return errors


class DismissErrorRequest(BaseModel):
    error_id: UUID  # Pydantic validates format; returns 422 for non-UUID values


@router.post("/{project_id}/build/errors/dismiss")
async def dismiss_build_error(
    project_id: UUID,
    body: DismissErrorRequest,
    user: dict = Depends(get_current_user),
):
    """Mark a build error as dismissed by the user."""
    from app.repos import build_repo as _br
    result = await _br.resolve_build_error(
        body.error_id,
        method="dismissed",
    )
    if not result:
        raise HTTPException(status_code=404, detail="Error not found")
    for k, v in result.items():
        if isinstance(v, UUID):
            result[k] = str(v)
        elif hasattr(v, "isoformat"):
            result[k] = v.isoformat()
    return result
