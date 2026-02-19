"""Build cycle repository — database ops for build_cycles table.

Phase 58.4 — links dossier → branch → seal as a lifecycle triple.
"""

from __future__ import annotations

from uuid import UUID

from app.repos.db import get_pool


async def create_build_cycle(
    project_id: UUID,
    repo_id: UUID,
    user_id: UUID,
    *,
    dossier_run_id: UUID | None = None,
    branch_name: str | None = None,
    baseline_sha: str | None = None,
) -> dict:
    """Insert a new active build cycle."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO build_cycles
            (project_id, repo_id, user_id, dossier_run_id,
             branch_name, baseline_sha, status)
        VALUES ($1, $2, $3, $4, $5, $6, 'active')
        RETURNING *
        """,
        project_id,
        repo_id,
        user_id,
        dossier_run_id,
        branch_name,
        baseline_sha,
    )
    return dict(row)


async def get_build_cycle(cycle_id: UUID) -> dict | None:
    """Fetch a single build cycle by ID."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM build_cycles WHERE id = $1",
        cycle_id,
    )
    return dict(row) if row else None


async def get_active_cycle(project_id: UUID) -> dict | None:
    """Return the currently active build cycle for a project, if any."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM build_cycles
        WHERE project_id = $1 AND status = 'active'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        project_id,
    )
    return dict(row) if row else None


async def get_cycles_for_project(
    project_id: UUID,
    limit: int = 20,
) -> list[dict]:
    """Return recent build cycles for a project, newest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM build_cycles
        WHERE project_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        project_id,
        limit,
    )
    return [dict(r) for r in rows]


async def seal_cycle(cycle_id: UUID, seal_id: UUID) -> dict:
    """Mark a build cycle as sealed with its certificate ID.

    Raises ValueError if the cycle is not in 'active' status.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE build_cycles
        SET status = 'sealed',
            seal_id = $2,
            sealed_at = now()
        WHERE id = $1 AND status = 'active'
        RETURNING *
        """,
        cycle_id,
        seal_id,
    )
    if row is None:
        raise ValueError(f"Build cycle {cycle_id} is not active or does not exist")
    return dict(row)


async def abandon_cycle(cycle_id: UUID) -> dict:
    """Mark a build cycle as abandoned.

    Raises ValueError if the cycle is not in 'active' status.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE build_cycles
        SET status = 'abandoned'
        WHERE id = $1 AND status = 'active'
        RETURNING *
        """,
        cycle_id,
    )
    if row is None:
        raise ValueError(f"Build cycle {cycle_id} is not active or does not exist")
    return dict(row)
