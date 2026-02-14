"""Repo service -- orchestrates repo connection, disconnection, and listing."""

from uuid import UUID

from app.clients.github_client import create_webhook, delete_webhook, list_user_repos
from app.config import settings
from app.repos.repo_repo import (
    create_repo,
    delete_repo,
    get_repo_by_github_id,
    get_repo_by_id,
    get_repos_by_user,
    get_repos_with_health,
)
from app.repos.user_repo import get_user_by_id


async def connect_repo(
    user_id: UUID,
    github_repo_id: int,
    full_name: str,
    default_branch: str,
) -> dict:
    """Connect a GitHub repo: register webhook, create DB record.

    Raises ValueError if the repo is already connected.
    """
    existing = await get_repo_by_github_id(github_repo_id)
    if existing is not None:
        raise ValueError(f"Repo {full_name} is already connected")

    user = await get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")

    access_token = user["access_token"]

    webhook_url = f"{settings.APP_URL}/webhooks/github"
    webhook_id = await create_webhook(
        access_token=access_token,
        full_name=full_name,
        webhook_url=webhook_url,
        webhook_secret=settings.GITHUB_WEBHOOK_SECRET,
    )

    repo = await create_repo(
        user_id=user_id,
        github_repo_id=github_repo_id,
        full_name=full_name,
        default_branch=default_branch,
        webhook_id=webhook_id,
        webhook_active=True,
    )

    return repo


async def disconnect_repo(user_id: UUID, repo_id: UUID) -> None:
    """Disconnect a repo: remove webhook, delete DB record and associated data.

    Raises ValueError if repo not found or user doesn't own it.
    """
    repo = await get_repo_by_id(repo_id)
    if repo is None:
        raise ValueError("Repo not found")

    if repo["user_id"] != user_id:
        raise ValueError("Not authorized to disconnect this repo")

    user = await get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")

    # Remove webhook from GitHub (best-effort)
    if repo.get("webhook_id"):
        try:
            await delete_webhook(
                access_token=user["access_token"],
                full_name=repo["full_name"],
                webhook_id=repo["webhook_id"],
            )
        except Exception:
            pass  # Webhook removal is best-effort

    await delete_repo(repo_id)


async def list_connected_repos(user_id: UUID) -> list[dict]:
    """List all connected repos for a user with computed health data."""
    repos = await get_repos_with_health(user_id)
    result = []
    for repo in repos:
        total = repo.get("total_count") or 0
        pass_count = repo.get("pass_count") or 0

        if total == 0:
            health = "pending"
            rate = None
        else:
            rate = pass_count / total
            if rate == 1.0:
                health = "green"
            elif rate >= 0.5:
                health = "yellow"
            else:
                health = "red"

        last_audit = repo.get("last_audit_at")

        result.append({
            "id": str(repo["id"]),
            "full_name": repo["full_name"],
            "default_branch": repo["default_branch"],
            "webhook_active": repo["webhook_active"],
            "health_score": health,
            "last_audit_at": last_audit.isoformat() if last_audit else None,
            "recent_pass_rate": rate,
        })
    return result


async def list_available_repos(user_id: UUID) -> list[dict]:
    """List GitHub repos available to connect (not yet connected).

    Fetches repos from GitHub and filters out already-connected ones.
    """
    user = await get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")

    github_repos = await list_user_repos(user["access_token"])

    connected = await get_repos_by_user(user_id)
    connected_ids = {r["github_repo_id"] for r in connected}

    return [r for r in github_repos if r["github_repo_id"] not in connected_ids]
