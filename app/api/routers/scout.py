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
from app.services.upgrade_executor import (
    execute_upgrade,
    get_available_commands,
    get_upgrade_status,
    prepare_upgrade_workspace,
    send_command,
    set_narrator_watching,
)
from app.repos.user_repo import get_user_by_id
from app.repos.scout_repo import get_scout_run

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/scout", tags=["scout"])


class ScoutRunRequest(BaseModel):
    hypothesis: str | None = Field(None, max_length=1000)


class DeepScanRequest(BaseModel):
    hypothesis: str | None = Field(None, max_length=1000)
    include_llm: bool = True


class UpgradePlanRequest(BaseModel):
    include_llm: bool = True


class UpgradeCommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=200)


@router.post("/{repo_id}/run")
async def trigger_scout(
    repo_id: UUID,
    body: ScoutRunRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger an on-demand Scout run against a connected repo."""
    try:
        return await start_scout_run(
            current_user["id"],
            repo_id,
            hypothesis=body.hypothesis if body else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Scout quick-scan trigger failed for repo %s", repo_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start scout scan",
        )


@router.post("/{repo_id}/deep-scan")
async def trigger_deep_scan(
    repo_id: UUID,
    body: DeepScanRequest | None = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Trigger a deep-scan Scout run for full project intelligence."""
    try:
        return await start_deep_scan(
            current_user["id"],
            repo_id,
            hypothesis=body.hypothesis if body else None,
            include_llm=body.include_llm if body else True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Scout deep-scan trigger failed for repo %s", repo_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start deep scan",
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
    try:
        return await generate_renovation_plan(
            current_user["id"],
            run_id,
            include_llm=body.include_llm if body else True,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Upgrade plan generation failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate upgrade plan",
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


@router.post("/runs/{run_id}/execute-upgrade")
async def trigger_execute_upgrade(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Start executing upgrade tasks from a renovation plan."""
    try:
        # Load user record to extract BYOK keys
        user = await get_user_by_id(current_user["id"])
        api_key = (user or {}).get("anthropic_api_key") or ""
        api_key_2 = (user or {}).get("anthropic_api_key_2") or ""

        return await execute_upgrade(
            current_user["id"],
            run_id,
            api_key=api_key,
            api_key_2=api_key_2,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Upgrade execution failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start upgrade execution",
        )


@router.get("/runs/{run_id}/upgrade-preview")
async def get_upgrade_preview(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return task list & repo info from renovation plan (no execution)."""
    import json as _json
    run = await get_scout_run(run_id)
    if run is None or str(run["user_id"]) != str(current_user["id"]):
        raise HTTPException(status_code=404, detail="Scout run not found")
    results = run.get("results")
    if isinstance(results, str):
        results = _json.loads(results)
    plan = (results or {}).get("renovation_plan")
    if not plan:
        raise HTTPException(status_code=404, detail="No renovation plan")

    tasks = plan.get("migration_recommendations", [])
    executive = plan.get("executive_brief", {})

    return {
        "run_id": str(run_id),
        "repo_name": run.get("repo_name", ""),
        "total_tasks": len(tasks),
        "tasks": [
            {
                "id": t.get("id", f"TASK-{i}"),
                "name": f"{t.get('from_state', '?')} \u2192 {t.get('to_state', '?')}",
                "priority": t.get("priority", "medium"),
                "effort": t.get("effort", "medium"),
                "forge_automatable": t.get("forge_automatable", False),
                "category": t.get("category", ""),
                "worker": "sonnet",
            }
            for i, t in enumerate(tasks)
        ],
        "executive_brief": {
            "headline": executive.get("headline", ""),
            "health_grade": executive.get("health_grade", ""),
        },
    }


@router.post("/runs/{run_id}/prepare-upgrade")
async def trigger_prepare_upgrade(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Clone the repo into a workspace, ready for /start."""
    try:
        user = await get_user_by_id(current_user["id"])
        access_token = (user or {}).get("access_token", "")
        return await prepare_upgrade_workspace(
            current_user["id"],
            run_id,
            access_token=access_token,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc),
        ) from exc
    except Exception:
        logger.exception("Prepare workspace failed for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to prepare workspace",
        )


@router.get("/runs/{run_id}/upgrade-status")
async def get_upgrade_execution_status(
    run_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get current upgrade execution status (polling fallback)."""
    st = get_upgrade_status(str(run_id))
    if st is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active upgrade execution for this run",
        )
    return {
        "run_id": st["run_id"],
        "status": st["status"],
        "total_tasks": st["total_tasks"],
        "completed_tasks": st["completed_tasks"],
        "current_task": st["current_task"],
        "repo_name": st.get("repo_name", ""),
        "narrator_enabled": st.get("narrator_enabled", False),
        "narrator_watching": st.get("narrator_watching", False),
        "tokens": st.get("tokens", {}),
        "started_at": st.get("started_at"),
        "completed_at": st.get("completed_at"),
        "logs": st.get("logs", [])[-50:],  # last 50 log entries
    }


@router.post("/runs/{run_id}/command")
async def send_upgrade_command(
    run_id: UUID,
    body: UpgradeCommandRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Send a slash command to a running upgrade session."""
    result = await send_command(
        str(current_user["id"]), str(run_id), body.command,
    )
    return result


@router.post("/runs/{run_id}/narrator")
async def toggle_narrator(
    run_id: UUID,
    body: dict,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Toggle narrator watching on/off for a running upgrade session."""
    watching = bool(body.get("watching", False))
    found = set_narrator_watching(str(run_id), watching)
    if not found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active upgrade execution for this run",
        )
    return {"narrator_watching": watching}


@router.get("/upgrade-commands")
async def list_upgrade_commands(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return available slash commands for IDE autocomplete."""
    return {"commands": get_available_commands()}
