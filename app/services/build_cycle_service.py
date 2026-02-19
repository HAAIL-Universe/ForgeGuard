"""Build cycle service — orchestrates the dossier → branch → seal lifecycle.

Phase 58.4 — each build cycle links an immutable dossier, an isolated
branch (``forge/build-{id}``), and an optional Forge Seal as a triple.
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.repos.build_cycle_repo import (
    abandon_cycle,
    create_build_cycle,
    get_active_cycle,
    get_build_cycle,
    get_cycles_for_project,
    seal_cycle,
)
from app.repos.scout_repo import (
    is_dossier_locked,
    link_dossier_to_cycle,
    lock_dossier,
)

logger = logging.getLogger(__name__)


async def start_build_cycle(
    project_id: UUID,
    repo_id: UUID,
    user_id: UUID,
    *,
    dossier_run_id: UUID | None = None,
    baseline_sha: str | None = None,
) -> dict:
    """Start a new build cycle for a project.

    - Ensures no other active cycle exists for this project.
    - If a dossier_run_id is provided, locks the dossier and links it.
    - Creates a ``forge/build-{cycle_id}`` branch name (actual branch
      creation is handled by the IDE layer in §39).

    Returns the created build_cycle dict.

    Raises
    ------
    ValueError – if an active cycle already exists.
    """
    existing = await get_active_cycle(project_id)
    if existing is not None:
        raise ValueError(
            f"Project {project_id} already has an active build cycle: "
            f"{existing['id']}"
        )

    # Lock the dossier if not already locked
    if dossier_run_id is not None:
        locked = await is_dossier_locked(dossier_run_id)
        if not locked:
            try:
                await lock_dossier(dossier_run_id)
                logger.info("Dossier %s locked for build cycle", dossier_run_id)
            except ValueError:
                logger.warning("Dossier %s already locked", dossier_run_id)

    cycle = await create_build_cycle(
        project_id=project_id,
        repo_id=repo_id,
        user_id=user_id,
        dossier_run_id=dossier_run_id,
        baseline_sha=baseline_sha,
    )

    cycle_id = cycle["id"]

    # Update branch name now that we know the ID
    branch_name = f"forge/build-{cycle_id}"
    from app.repos.db import get_pool
    pool = await get_pool()
    await pool.execute(
        "UPDATE build_cycles SET branch_name = $2 WHERE id = $1",
        cycle_id,
        branch_name,
    )
    cycle["branch_name"] = branch_name

    # Link the dossier back to this cycle
    if dossier_run_id is not None:
        await link_dossier_to_cycle(dossier_run_id, cycle_id)

    logger.info(
        "Build cycle %s started for project %s (branch: %s)",
        cycle_id, project_id, branch_name,
    )

    return cycle


async def finish_build_cycle(
    cycle_id: UUID,
    seal_id: UUID,
) -> dict:
    """Seal a build cycle with the given certificate ID.

    Raises ValueError if the cycle is not active.
    """
    cycle = await seal_cycle(cycle_id, seal_id)
    logger.info("Build cycle %s sealed with certificate %s", cycle_id, seal_id)
    return cycle


async def abandon_build_cycle(cycle_id: UUID) -> dict:
    """Abandon an active build cycle without sealing.

    Raises ValueError if the cycle is not active.
    """
    cycle = await abandon_cycle(cycle_id)
    logger.info("Build cycle %s abandoned", cycle_id)
    return cycle


async def get_current_cycle(project_id: UUID) -> dict | None:
    """Return the active build cycle for a project, or None."""
    return await get_active_cycle(project_id)


async def get_cycle_detail(cycle_id: UUID) -> dict | None:
    """Return a single build cycle by ID."""
    return await get_build_cycle(cycle_id)


async def list_project_cycles(
    project_id: UUID,
    limit: int = 20,
) -> list[dict]:
    """Return recent build cycles for a project."""
    return await get_cycles_for_project(project_id, limit)
