"""Audit router -- governance audit trigger endpoint."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.services.audit_service import run_governance_audit

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/run")
async def trigger_governance_audit(
    claimed_files: str = Query(
        ..., description="Comma-separated list of claimed file paths"
    ),
    phase: str = Query("unknown", description="Phase identifier"),
    _user: dict = Depends(get_current_user),
) -> dict:
    """Trigger a governance audit run programmatically.

    Runs all A1-A9 blocking checks and W1-W3 warnings.
    Returns structured results.
    """
    files = [f.strip() for f in claimed_files.split(",") if f.strip()]
    result = run_governance_audit(claimed_files=files, phase=phase)
    return result
