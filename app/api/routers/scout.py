"""Scout router -- on-demand audit scanning for connected repos."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.scout_service import (
    get_scout_detail,
    get_scout_history,
    start_scout_run,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scout", tags=["scout"])


class ScoutRunRequest(BaseModel):
    hypothesis: str | None = Field(None, max_length=1000)


@router.post("/{repo_id}/run")
async def trigger_scout(
    repo_id: UUID,
    body: ScoutRunRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger an on-demand Scout run against a connected repo."""
    try:
        result = await start_scout_run(
            current_user["id"],
            repo_id,
            hypothesis=body.hypothesis if body else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return result


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
    try:
        detail = await get_scout_detail(current_user["id"], run_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
    return detail


@router.get("/{repo_id}/history")
async def scout_repo_history(
    repo_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get recent Scout runs for a specific repo."""
    runs = await get_scout_history(current_user["id"], repo_id=repo_id)
    return {"runs": runs}
