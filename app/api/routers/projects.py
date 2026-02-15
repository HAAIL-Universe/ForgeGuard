"""Projects router -- project CRUD, questionnaire chat, contract management."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.project_service import (
    create_new_project,
    delete_user_project,
    generate_contracts,
    get_contract,
    get_project_detail,
    get_questionnaire_state,
    list_contracts,
    list_user_projects,
    process_questionnaire_message,
    update_contract,
)

logger = logging.getLogger(__name__)

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
    project = await create_new_project(
        user_id=current_user["id"],
        name=body.name,
        description=body.description,
        repo_id=repo_id,
        local_path=body.local_path,
    )
    return {
        "id": str(project["id"]),
        "name": project["name"],
        "description": project["description"],
        "status": project["status"],
        "created_at": project["created_at"],
    }


@router.get("")
async def list_projects(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List user's projects."""
    projects = await list_user_projects(current_user["id"])
    return {
        "items": [
            {
                "id": str(p["id"]),
                "name": p["name"],
                "description": p["description"],
                "status": p["status"],
                "created_at": p["created_at"],
                "updated_at": p["updated_at"],
            }
            for p in projects
        ]
    }


@router.get("/{project_id}")
async def get_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get project detail with contract status."""
    try:
        return await get_project_detail(current_user["id"], project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


@router.delete("/{project_id}")
async def remove_project(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Delete a project."""
    try:
        await delete_user_project(current_user["id"], project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
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
    try:
        return await process_questionnaire_message(
            user_id=current_user["id"],
            project_id=project_id,
            message=body.message,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail)


@router.get("/{project_id}/questionnaire/state")
async def questionnaire_progress(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Current questionnaire progress."""
    try:
        return await get_questionnaire_state(current_user["id"], project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )


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
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail)
    return {"contracts": contracts}


@router.get("/{project_id}/contracts")
async def list_project_contracts(
    project_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List generated contracts."""
    try:
        contracts = await list_contracts(current_user["id"], project_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        )
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


@router.get("/{project_id}/contracts/{contract_type}")
async def get_project_contract(
    project_id: UUID,
    contract_type: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """View a single contract."""
    try:
        return await get_contract(current_user["id"], project_id, contract_type)
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail)


@router.put("/{project_id}/contracts/{contract_type}")
async def edit_contract(
    project_id: UUID,
    contract_type: str,
    body: UpdateContractRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Edit a contract before build."""
    try:
        result = await update_contract(
            current_user["id"], project_id, contract_type, body.content
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail)
    return {
        "id": str(result["id"]),
        "contract_type": result["contract_type"],
        "content": result["content"],
        "version": result["version"],
        "updated_at": result["updated_at"],
    }
