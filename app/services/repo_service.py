"""Repo service -- orchestrates repo connection, disconnection, and listing."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.clients.github_client import (
    create_github_repo,
    create_webhook,
    delete_webhook,
    get_repo_health,
    list_commits,
    list_user_repos,
)
from app.config import settings
from app.repos.repo_repo import (
    create_repo,
    delete_repo,
    get_repo_by_github_id,
    get_repo_by_id,
    get_repos_by_user,
    get_repos_with_health,
    update_repo_full_name,
    update_repo_health,
)
from app.repos.user_repo import get_user_by_id
from app.ws_manager import manager

logger = logging.getLogger(__name__)


async def create_and_connect_repo(
    user_id: UUID,
    name: str,
    description: str | None = None,
    private: bool = False,
) -> dict:
    """Create a new GitHub repo and connect it to ForgeGuard.

    Creates the repo on GitHub, registers a push webhook, and saves
    the connection to the database. Returns the connected repo row.
    """
    user = await get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")

    repo_info = await create_github_repo(
        access_token=user["access_token"],
        name=name,
        description=description,
        private=private,
    )

    webhook_url = f"{settings.APP_URL}/webhooks/github"
    webhook_id = await create_webhook(
        access_token=user["access_token"],
        full_name=repo_info["full_name"],
        webhook_url=webhook_url,
        webhook_secret=settings.GITHUB_WEBHOOK_SECRET,
    )

    repo = await create_repo(
        user_id=user_id,
        github_repo_id=repo_info["github_repo_id"],
        full_name=repo_info["full_name"],
        default_branch=repo_info["default_branch"],
        webhook_id=webhook_id,
        webhook_active=True,
    )
    return repo


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

        last_health = repo.get("last_health_check_at")
        commit_at = repo.get("latest_commit_at")

        result.append({
            "id": str(repo["id"]),
            "full_name": repo["full_name"],
            "default_branch": repo["default_branch"],
            "webhook_active": repo["webhook_active"],
            "health_score": health,
            "last_audit_at": last_audit.isoformat() if last_audit else None,
            "recent_pass_rate": rate,
            # Health check fields
            "repo_status": repo.get("repo_status", "connected"),
            "last_health_check_at": last_health.isoformat() if last_health else None,
            "latest_commit_sha": repo.get("latest_commit_sha"),
            "latest_commit_message": repo.get("latest_commit_message"),
            "latest_commit_at": commit_at.isoformat() if commit_at else None,
            "latest_commit_author": repo.get("latest_commit_author"),
        })
    return result


async def _check_single_repo(user: dict, repo: dict) -> None:
    """Run a health check for one repo against GitHub and persist the result.

    Silently swallows unexpected exceptions so a single failure doesn't abort
    the batch.
    """
    access_token = user["access_token"]
    owner_name = repo["full_name"]  # "owner/name"
    repo_id = repo["id"]
    now = datetime.now(timezone.utc)

    repo_status = "connected"
    commit_sha = None
    commit_message = None
    commit_at = None
    commit_author = None

    try:
        status_code, gh_data = await get_repo_health(access_token, owner_name)

        if status_code == 404:
            await update_repo_health(repo_id, {
                "repo_status": "deleted",
                "last_health_check_at": now,
            })
            return
        if status_code in (401, 403):
            await update_repo_health(repo_id, {
                "repo_status": "inaccessible",
                "last_health_check_at": now,
            })
            return
        if status_code != 200 or gh_data is None:
            logger.warning("Health check for %s returned %s — skipping", owner_name, status_code)
            return

        if gh_data.get("archived"):
            repo_status = "archived"
        elif gh_data.get("full_name") and gh_data["full_name"] != owner_name:
            repo_status = "connected"
            await update_repo_full_name(repo_id, gh_data["full_name"])
        else:
            repo_status = "connected"

        # Fetch latest commit
        try:
            commits = await list_commits(access_token, owner_name, per_page=1, max_pages=1)
            if commits:
                c = commits[0]
                commit_sha = c["sha"]
                commit_message = (c.get("message") or "").split("\n")[0][:500]
                commit_author = c.get("author", "")
                raw_date = c.get("date", "")
                if raw_date:
                    try:
                        commit_at = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    except ValueError:
                        commit_at = None
        except Exception as exc:
            logger.debug("Could not fetch commits for %s: %s", owner_name, exc)

        await update_repo_health(repo_id, {
            "repo_status": repo_status,
            "last_health_check_at": now,
            "latest_commit_sha": commit_sha,
            "latest_commit_message": commit_message,
            "latest_commit_at": commit_at,
            "latest_commit_author": commit_author,
        })

    except Exception as exc:
        logger.warning("Unexpected error in health check for %s: %s", owner_name, exc)


async def run_repo_health_check(user_id: UUID) -> None:
    """Background task: check all repos for a user and emit WS update on completion."""
    user = await get_user_by_id(user_id)
    if not user:
        return
    repos = await get_repos_by_user(user_id)
    await asyncio.gather(
        *[_check_single_repo(user, r) for r in repos],
        return_exceptions=True,
    )
    await manager.send_to_user(str(user_id), {
        "type": "repos_health_updated",
        "payload": {"checked": len(repos)},
    })


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


async def list_all_user_repos(user_id: UUID) -> list[dict]:
    """List all GitHub repos with connection status.

    Returns every repo the user can access on GitHub, each annotated
    with ``connected`` (bool) and ``repo_id`` (ForgeGuard UUID or None).
    """
    user = await get_user_by_id(user_id)
    if user is None:
        raise ValueError("User not found")

    github_repos = await list_user_repos(user["access_token"])

    connected = await get_repos_by_user(user_id)
    connected_map = {r["github_repo_id"]: r for r in connected}

    result: list[dict] = []
    for repo in github_repos:
        entry = connected_map.get(repo["github_repo_id"])
        result.append({
            **repo,
            "connected": entry is not None,
            "repo_id": str(entry["id"]) if entry else None,
        })
    return result
