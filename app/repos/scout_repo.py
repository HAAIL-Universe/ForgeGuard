"""Scout repository -- database ops for scout_runs table."""

import json
from uuid import UUID

from app.repos.db import get_pool


async def create_scout_run(
    repo_id: UUID,
    user_id: UUID,
    hypothesis: str | None = None,
    scan_type: str = "quick",
) -> dict:
    """Insert a new pending scout run."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO scout_runs (repo_id, user_id, hypothesis, status, scan_type)
        VALUES ($1, $2, $3, 'running', $4)
        RETURNING id, repo_id, user_id, status, hypothesis, scan_type,
                  checks_passed, checks_failed, checks_warned,
                  started_at, completed_at
        """,
        repo_id,
        user_id,
        hypothesis,
        scan_type,
    )
    return dict(row)


async def update_scout_run(
    run_id: UUID,
    *,
    status: str,
    results: dict | None = None,
    checks_passed: int = 0,
    checks_failed: int = 0,
    checks_warned: int = 0,
) -> dict:
    """Update a scout run with results."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE scout_runs
        SET status = $2,
            results = $3::jsonb,
            checks_passed = $4,
            checks_failed = $5,
            checks_warned = $6,
            completed_at = CASE WHEN $2 IN ('completed', 'error') THEN now() ELSE completed_at END
        WHERE id = $1
        RETURNING id, repo_id, user_id, status, hypothesis, scan_type,
                  results, checks_passed, checks_failed, checks_warned,
                  started_at, completed_at
        """,
        run_id,
        status,
        json.dumps(results) if results else None,
        checks_passed,
        checks_failed,
        checks_warned,
    )
    return dict(row) if row else {}


async def get_scout_runs_by_user(
    user_id: UUID,
    limit: int = 20,
) -> list[dict]:
    """Recent scout runs across all repos for a user."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT s.id, s.repo_id, r.full_name AS repo_name,
               s.status, s.hypothesis, s.scan_type,
               s.checks_passed, s.checks_failed, s.checks_warned,
               s.started_at, s.completed_at
        FROM scout_runs s
        JOIN repos r ON r.id = s.repo_id
        WHERE s.user_id = $1
        ORDER BY s.started_at DESC
        LIMIT $2
        """,
        user_id,
        limit,
    )
    return [dict(r) for r in rows]


async def get_scout_runs_by_repo(
    repo_id: UUID,
    user_id: UUID,
    limit: int = 20,
) -> list[dict]:
    """Recent scout runs for a specific repo."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT s.id, s.repo_id, r.full_name AS repo_name,
               s.status, s.hypothesis, s.scan_type,
               s.checks_passed, s.checks_failed, s.checks_warned,
               s.started_at, s.completed_at
        FROM scout_runs s
        JOIN repos r ON r.id = s.repo_id
        WHERE s.repo_id = $1 AND s.user_id = $2
        ORDER BY s.started_at DESC
        LIMIT $3
        """,
        repo_id,
        user_id,
        limit,
    )
    return [dict(r) for r in rows]


async def get_scout_run(run_id: UUID) -> dict | None:
    """Get a single scout run with full results."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT s.*, r.full_name AS repo_name
        FROM scout_runs s
        JOIN repos r ON r.id = s.repo_id
        WHERE s.id = $1
        """,
        run_id,
    )
    return dict(row) if row else None
