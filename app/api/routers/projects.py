"""Projects router -- project CRUD, questionnaire chat, contract management."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.repos import certificate_repo
from app.services.certificate_aggregator import aggregate_certificate_data
from app.services.certificate_renderer import render_certificate
from app.services.certificate_scorer import compute_certificate_scores
from app.services.project_service import (
    ContractCancelled,
    cancel_contract_generation,
    create_new_project,
    delete_user_project,
    generate_contracts,
    get_contract,
    get_contract_version,
    get_project_detail,
    get_questionnaire_state,
    list_contract_versions,
    list_contracts,
    list_user_projects,
    process_questionnaire_message,

    reset_questionnaire,
    update_contract,
)

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateProjectRequest(BaseModel):
    """Request body for creating a project."""

    name: str = Field(..., min_length=1, max_length=255, description="Project name")
    description: str | None = Field(
        None, max_length=2000, description="Project description"
    )
    repo_id: str | None = Field(
        None, description="Connected ForgeGuard repo UUID to link"
    )
    local_path: str | None = Field(
        None, max_length=1000, description="Local filesystem path for local projects"
    )
    build_mode: str = Field(
        "full", description="Build mode: 'mini' (2 phases) or 'full' (6-12 phases)"
    )


class QuestionnaireMessageRequest(BaseModel):
    """Request body for sending a questionnaire message."""

    message: str = Field(..., min_length=1, description="User message")


class UpdateContractRequest(BaseModel):
    """Request body for updating a contract."""

    content: str = Field(..., min_length=1, description="Updated contract content")


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


@router.post("")
async def create_project(
    body: CreateProjectRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create a new project."""
    repo_id = UUID(body.repo_id) if body.repo_id else None
    bm = body.build_mode if body.build_mode in ("mini", "full") else "full"
    project = await create_new_project(
        user_id=current_user["id"],
        name=body.name,
        description=body.description,
        repo_id=repo_id,
        local_path=body.local_path,
        build_mode=bm,
    )
    return {
        "id": str(project["id"]),
        "name": project["name"],
        "description": project["description"],
        "status": project["status"],
        "build_mode": project.get("build_mode", "full"),
        "created_at": project["created_at"],
    }


@router.get("")
async def list_projects(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List user's projects."""
    projects = await list_user_projects(current_user["id"])
    page = projects[offset : offset + limit]
    return {
        "items": [
            {
                "id": str(p["id"]),
                "name": p["name"],
                "description": p["description"],
                "status": p["status"],
                "build_mode": p.get("build_mode", "full"),
                "latest_build_status": p.get("latest_build_status"),
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
            for p in page
        ],
        "total": len(projects),
    }


@router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get project detail with contract status."""
    return await get_project_detail(current_user["id"], project_id)


@router.delete("/{project_id}")
async def remove_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Delete a project."""
    await delete_user_project(current_user["id"], project_id)
    return {"status": "deleted"}


# ---------------------------------------------------------------------------
# Questionnaire
# ---------------------------------------------------------------------------


@router.post("/{project_id}/questionnaire")
async def questionnaire_message(
    project_id: UUID,
    body: QuestionnaireMessageRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Send a message to the questionnaire chat."""
    return await process_questionnaire_message(
        user_id=current_user["id"],
        project_id=project_id,
        message=body.message,
    )


@router.get("/{project_id}/questionnaire/state")
async def questionnaire_progress(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Current questionnaire progress."""
    return await get_questionnaire_state(current_user["id"], project_id)


@router.delete("/{project_id}/questionnaire")
async def questionnaire_reset(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Reset questionnaire to start over."""
    return await reset_questionnaire(current_user["id"], project_id)


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------


@router.post("/{project_id}/contracts/generate")
async def gen_contracts(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Generate all contract files from completed questionnaire answers."""
    try:
        contracts = await generate_contracts(current_user["id"], project_id)
    except ContractCancelled:
        # User cancelled — return 200 with cancelled flag (not an error)
        return {"cancelled": True, "contracts": []}
    except ValueError as exc:
        msg = str(exc)
        if "already in progress" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return {"contracts": contracts}


@router.post("/{project_id}/contracts/cancel")
async def cancel_contracts(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Cancel an in-progress contract generation."""
    return await cancel_contract_generation(current_user["id"], project_id)


@router.get("/{project_id}/contracts")
async def list_project_contracts(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List generated contracts."""
    contracts = await list_contracts(current_user["id"], project_id)
    return {
        "items": [
            {
                "id": str(c["id"]),
                "project_id": str(c["project_id"]),
                "contract_type": c["contract_type"],
                "version": c["version"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
            }
            for c in contracts
        ]
    }


@router.get("/{project_id}/contracts/history")
async def list_contract_history(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List all snapshot batches (previous contract versions)."""
    batches = await list_contract_versions(current_user["id"], project_id)
    return {"items": batches}


@router.get("/{project_id}/contracts/history/{batch}")
async def get_contract_history_batch(
    project_id: UUID,
    batch: int,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get all contracts for a specific snapshot batch."""
    contracts = await get_contract_version(current_user["id"], project_id, batch)
    return {
        "items": [
            {
                "id": str(c["id"]),
                "project_id": str(c["project_id"]),
                "batch": c["batch"],
                "contract_type": c["contract_type"],
                "content": c["content"],
                "created_at": c["created_at"],
            }
            for c in contracts
        ]
    }


@router.get("/{project_id}/contracts/{contract_type}")
async def get_project_contract(
    project_id: UUID,
    contract_type: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """View a single contract."""
    return await get_contract(current_user["id"], project_id, contract_type)


@router.put("/{project_id}/contracts/{contract_type}")
async def edit_contract(
    project_id: UUID,
    contract_type: str,
    body: UpdateContractRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Edit a contract before build."""
    result = await update_contract(
        current_user["id"], project_id, contract_type, body.content
    )
    return {
        "id": str(result["id"]),
        "contract_type": result["contract_type"],
        "content": result["content"],
        "version": result["version"],
        "updated_at": result["updated_at"],
    }


# ── Forge Seal: Build Certificate ────────────────────────────────


@router.get("/{project_id}/certificate")
async def get_certificate(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the stored Forge Seal build certificate (JSON). Falls back to live recompute for pre-migration builds."""
    stored = await certificate_repo.get_latest_certificate(project_id)
    if stored:
        return render_certificate(stored["scores_json"], "json")
    data = await aggregate_certificate_data(project_id, current_user["id"])
    scores = compute_certificate_scores(data)
    return render_certificate(scores, "json")


@router.get("/{project_id}/certificate/html", response_class=HTMLResponse)
async def get_certificate_html(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> HTMLResponse:
    """Return the stored Forge Seal build certificate (styled HTML). Falls back to live recompute for pre-migration builds."""
    stored = await certificate_repo.get_latest_certificate(project_id)
    if stored and stored.get("certificate_html"):
        return HTMLResponse(content=stored["certificate_html"])
    data = await aggregate_certificate_data(project_id, current_user["id"])
    scores = compute_certificate_scores(data)
    html = render_certificate(scores, "html")
    return HTMLResponse(content=html)


@router.get("/{project_id}/certificate/text")
async def get_certificate_text(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the stored Forge Seal build certificate (plain text). Falls back to live recompute for pre-migration builds."""
    stored = await certificate_repo.get_latest_certificate(project_id)
    if stored:
        text = render_certificate(stored["scores_json"], "text")
        return {"format": "text", "certificate": text}
    data = await aggregate_certificate_data(project_id, current_user["id"])
    scores = compute_certificate_scores(data)
    text = render_certificate(scores, "text")
    return {"format": "text", "certificate": text}


# ---------------------------------------------------------------------------
# Phase 58: Build Cycle endpoints
# ---------------------------------------------------------------------------

@router.get("/{project_id}/build-cycle")
async def get_active_build_cycle(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the active build cycle for a project, or null."""
    from app.services.build_cycle_service import get_current_cycle

    cycle = await get_current_cycle(str(project_id))
    if cycle is None:
        return {"cycle": None}
    return {"cycle": cycle}


@router.get("/{project_id}/build-cycles")
async def list_build_cycles(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List all build cycles for a project."""
    from app.services.build_cycle_service import list_project_cycles

    cycles = await list_project_cycles(str(project_id))
    return {"items": cycles}
