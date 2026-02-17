"""Scout router -- on-demand audit scanning for connected repos."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.scout_service import (
    get_scout_detail,
    get_scout_dossier,
    get_scout_history,
    get_scout_score_history,
    start_deep_scan,
    start_scout_run,
)
from app.services.upgrade_service import (
    generate_renovation_plan,
    get_renovation_plan,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scout", tags=["scout"])


class ScoutRunRequest(BaseModel):
    hypothesis: str | None = Field(None, max_length=1000)


class DeepScanRequest(BaseModel):
    hypothesis: str | None = Field(None, max_length=1000)
    include_llm: bool = True


class UpgradePlanRequest(BaseModel):
    include_llm: bool = True


@router.post("/{repo_id}/run")
async def trigger_scout(
    repo_id: UUID,
    body: ScoutRunRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger an on-demand Scout run against a connected repo."""
    return await start_scout_run(
        current_user["id"],
        repo_id,
        hypothesis=body.hypothesis if body else None,
    )


@router.post("/{repo_id}/deep-scan")
async def trigger_deep_scan(
    repo_id: UUID,
    body: DeepScanRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger a deep-scan Scout run for full project intelligence."""
    return await start_deep_scan(
        current_user["id"],
        repo_id,
        hypothesis=body.hypothesis if body else None,
        include_llm=body.include_llm if body else True,
    )


@router.get("/history")
async def scout_history(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get recent Scout runs for the current user."""
    runs = await get_scout_history(current_user["id"])
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def scout_run_detail(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get full detail for a Scout run."""
    return await get_scout_detail(current_user["id"], run_id)


@router.get("/runs/{run_id}/dossier")
async def scout_run_dossier(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get the full dossier from a completed deep-scan run."""
    dossier = await get_scout_dossier(current_user["id"], run_id)
    if dossier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No dossier available for this run",
        )
    return dossier


@router.get("/{repo_id}/history")
async def scout_repo_history(
    repo_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get recent Scout runs for a specific repo."""
    runs = await get_scout_history(current_user["id"], repo_id=repo_id)
    return {"runs": runs}


@router.get("/{repo_id}/score-history")
async def scout_score_history(
    repo_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get computed score history for score-over-time charts."""
    history = await get_scout_score_history(current_user["id"], repo_id)
    return {"history": history}


# ---------------------------------------------------------------------------
# Upgrade-plan endpoints (Phase 37)
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/upgrade-plan")
async def trigger_upgrade_plan(
    run_id: UUID,
    body: UpgradePlanRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Generate an Upgrade / Renovation Plan for a completed deep-scan run."""
    return await generate_renovation_plan(
        current_user["id"],
        run_id,
        include_llm=body.include_llm if body else True,
    )


@router.get("/runs/{run_id}/upgrade-plan")
async def get_upgrade_plan(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Retrieve a previously generated Upgrade / Renovation Plan."""
    plan = await get_renovation_plan(current_user["id"], run_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No upgrade plan available for this run. Generate one first.",
        )
    return plan
