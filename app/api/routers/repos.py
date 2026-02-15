"""Repos router -- connect, disconnect, list repos, and audit results."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.services.audit_service import backfill_repo_commits, get_audit_detail, get_repo_audits
from app.services.repo_service import (
    connect_repo,
    create_and_connect_repo,
    disconnect_repo,
    list_all_user_repos,
    list_available_repos,
    list_connected_repos,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/repos", tags=["repos"])


class CreateRepoRequest(BaseModel):
    """Request body for creating a new GitHub repo."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9._-]+$",
        description="Repository name (no spaces)",
    )
    description: str | None = Field(
        None,
        max_length=500,
        description="Short repo description",
    )
    private: bool = Field(False, description="Create as private repo")


class ConnectRepoRequest(BaseModel):
    """Request body for connecting a GitHub repo."""

    github_repo_id: int = Field(..., ge=1, description="GitHub repo numeric ID")
    full_name: str = Field(
        ...,
        min_length=3,
        max_length=200,
        pattern=r"^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$",
        description="GitHub full name, e.g. owner/repo",
    )
    default_branch: str = Field(
        ...,
        min_length=1,
        max_length=100,
        pattern=r"^[a-zA-Z0-9._/-]+$",
        description="Default branch name, e.g. main",
    )


@router.get("")
async def list_repos(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List connected repos for the authenticated user."""
    items = await list_connected_repos(current_user["id"])
    return {"items": items}


@router.get("/available")
async def list_available(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List GitHub repos available to connect (not yet connected)."""
    items = await list_available_repos(current_user["id"])
    return {"items": items}


@router.get("/all")
async def list_all(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List all GitHub repos with connection status."""
    items = await list_all_user_repos(current_user["id"])
    return {"items": items}


@router.post("/create")
async def create_repo(
    body: CreateRepoRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Create a new GitHub repo and connect it to ForgeGuard."""
    try:
        repo = await create_and_connect_repo(
            user_id=current_user["id"],
            name=body.name,
            description=body.description,
            private=body.private,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        logger.exception("Failed to create GitHub repo %s", body.name)
        detail = str(exc) if str(exc) else "Failed to create repo on GitHub"
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=detail,
        )

    return {
        "id": str(repo["id"]),
        "full_name": repo["full_name"],
        "webhook_active": repo["webhook_active"],
    }


@router.post("/connect")
async def connect(
    body: ConnectRepoRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Connect a GitHub repo -- register webhook and start monitoring."""
    try:
        repo = await connect_repo(
            user_id=current_user["id"],
            github_repo_id=body.github_repo_id,
            full_name=body.full_name,
            default_branch=body.default_branch,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "already connected" in detail
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=detail)
    except Exception:
        logger.exception("Failed to register webhook for %s", body.full_name)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to register webhook with GitHub",
        )

    return {
        "id": str(repo["id"]),
        "full_name": repo["full_name"],
        "webhook_active": repo["webhook_active"],
    }


@router.delete("/{repo_id}/disconnect")
async def disconnect(
    repo_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Disconnect a repo -- remove webhook and stop monitoring."""
    try:
        await disconnect_repo(
            user_id=current_user["id"],
            repo_id=repo_id,
        )
    except ValueError as exc:
        detail = str(exc)
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in detail.lower()
            else status.HTTP_403_FORBIDDEN
        )
        raise HTTPException(status_code=code, detail=detail)
    except Exception:
        logger.exception("Failed to disconnect repo %s", repo_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disconnect repo",
        )

    return {"status": "disconnected"}


@router.post("/{repo_id}/sync")
async def sync_commits(
    repo_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Backfill missed commits from GitHub and run audits on them."""
    try:
        result = await backfill_repo_commits(
            repo_id=repo_id,
            user_id=current_user["id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except Exception:
        logger.exception("Failed to sync commits for repo %s", repo_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to sync commits from GitHub",
        )
    return result


@router.get("/{repo_id}/audits")
async def list_audits(
    repo_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: dict = Depends(get_current_user),
) -> dict:
    """List audit runs for a repo, newest first."""
    try:
        items, total = await get_repo_audits(
            repo_id=repo_id,
            user_id=current_user["id"],
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return {"items": items, "total": total}


@router.get("/{repo_id}/audits/{audit_id}")
async def get_audit(
    repo_id: UUID,
    audit_id: UUID,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Get full audit detail including all check results."""
    try:
        detail = await get_audit_detail(
            repo_id=repo_id,
            audit_id=audit_id,
            user_id=current_user["id"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    return detail
