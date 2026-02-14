"""Repos router -- connect, disconnect, and list GitHub repositories."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.services.repo_service import (
    connect_repo,
    disconnect_repo,
    list_available_repos,
    list_connected_repos,
)

router = APIRouter(prefix="/repos", tags=["repos"])


class ConnectRepoRequest(BaseModel):
    """Request body for connecting a GitHub repo."""

    github_repo_id: int
    full_name: str
    default_branch: str


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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to disconnect repo",
        )

    return {"status": "disconnected"}
