"""Repo repository -- database reads and writes for the repos table."""

from uuid import UUID

from app.repos.db import get_pool


async def create_repo(
    user_id: UUID,
    github_repo_id: int,
    full_name: str,
    default_branch: str,
    webhook_id: int | None = None,
    webhook_active: bool = False,
) -> dict:
    """Insert a new connected repo. Returns the created row as a dict."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO repos (user_id, github_repo_id, full_name, default_branch, webhook_id, webhook_active)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, user_id, github_repo_id, full_name, default_branch,
                  webhook_id, webhook_active, created_at, updated_at
        """,
        user_id,
        github_repo_id,
        full_name,
        default_branch,
        webhook_id,
        webhook_active,
    )
    return dict(row)


async def get_repos_by_user(user_id: UUID) -> list[dict]:
    """Fetch all connected repos for a user."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, user_id, github_repo_id, full_name, default_branch,
               webhook_id, webhook_active, created_at, updated_at
        FROM repos
        WHERE user_id = $1
        ORDER BY created_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def get_repo_by_id(repo_id: UUID) -> dict | None:
    """Fetch a repo by primary key. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, github_repo_id, full_name, default_branch,
               webhook_id, webhook_active, created_at, updated_at
        FROM repos
        WHERE id = $1
        """,
        repo_id,
    )
    return dict(row) if row else None


async def get_repo_by_github_id(github_repo_id: int) -> dict | None:
    """Fetch a repo by its GitHub repo ID. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_id, github_repo_id, full_name, default_branch,
               webhook_id, webhook_active, created_at, updated_at
        FROM repos
        WHERE github_repo_id = $1
        """,
        github_repo_id,
    )
    return dict(row) if row else None


async def delete_repo(repo_id: UUID) -> bool:
    """Delete a repo by primary key. Returns True if a row was deleted."""
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM repos WHERE id = $1",
        repo_id,
    )
    return result == "DELETE 1"


async def get_repos_with_health(user_id: UUID) -> list[dict]:
    """Fetch repos with recent audit health data for a user.

    Returns each repo with last_audit_at and pass/total counts from the
    10 most recent completed audit runs.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT
            r.id, r.user_id, r.github_repo_id, r.full_name,
            r.default_branch, r.webhook_id, r.webhook_active,
            r.created_at, r.updated_at,
            h.last_audit_at,
            h.pass_count,
            h.total_count
        FROM repos r
        LEFT JOIN LATERAL (
            SELECT
                max(a.completed_at) AS last_audit_at,
                count(*) FILTER (WHERE a.overall_result = 'PASS') AS pass_count,
                count(*) AS total_count
            FROM (
                SELECT overall_result, completed_at
                FROM audit_runs
                WHERE repo_id = r.id AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 10
            ) a
        ) h ON true
        WHERE r.user_id = $1
        ORDER BY r.created_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def update_webhook(
    repo_id: UUID,
    webhook_id: int | None,
    webhook_active: bool,
) -> None:
    """Update the webhook info for a repo."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE repos SET webhook_id = $2, webhook_active = $3, updated_at = now()
        WHERE id = $1
        """,
        repo_id,
        webhook_id,
        webhook_active,
    )
