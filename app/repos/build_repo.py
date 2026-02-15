"""Build repository -- database reads and writes for builds, build_logs, and build_costs tables."""

import json
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID

from app.repos.db import get_pool


# ---------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------


async def create_build(project_id: UUID) -> dict:
    """Create a new build record in pending status."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO builds (project_id, phase, status)
        VALUES ($1, 'Phase 0', 'pending')
        RETURNING id, project_id, phase, status, started_at, completed_at,
                  loop_count, error_detail, created_at
        """,
        project_id,
    )
    return dict(row)


async def get_build_by_id(build_id: UUID) -> dict | None:
    """Fetch a single build by ID."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, project_id, phase, status, started_at, completed_at,
               loop_count, error_detail, created_at
        FROM builds WHERE id = $1
        """,
        build_id,
    )
    return dict(row) if row else None


async def get_latest_build_for_project(project_id: UUID) -> dict | None:
    """Fetch the most recent build for a project."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, project_id, phase, status, started_at, completed_at,
               loop_count, error_detail, created_at
        FROM builds WHERE project_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        project_id,
    )
    return dict(row) if row else None


async def update_build_status(
    build_id: UUID,
    status: str,
    *,
    phase: str | None = None,
    started_at: datetime | None = None,
    completed_at: datetime | None = None,
    error_detail: str | None = None,
) -> None:
    """Update build status and optional fields."""
    pool = await get_pool()
    sets = ["status = $2"]
    params: list = [build_id, status]
    idx = 3

    if phase is not None:
        sets.append(f"phase = ${idx}")
        params.append(phase)
        idx += 1
    if started_at is not None:
        sets.append(f"started_at = ${idx}")
        params.append(started_at)
        idx += 1
    if completed_at is not None:
        sets.append(f"completed_at = ${idx}")
        params.append(completed_at)
        idx += 1
    if error_detail is not None:
        sets.append(f"error_detail = ${idx}")
        params.append(error_detail)
        idx += 1

    query = f"UPDATE builds SET {', '.join(sets)} WHERE id = $1"
    await pool.execute(query, *params)


async def increment_loop_count(build_id: UUID) -> int:
    """Increment the loop counter and return the new value."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE builds SET loop_count = loop_count + 1
        WHERE id = $1 RETURNING loop_count
        """,
        build_id,
    )
    return row["loop_count"] if row else 0


async def cancel_build(build_id: UUID) -> bool:
    """Cancel an active build. Returns True if updated."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    result = await pool.execute(
        """
        UPDATE builds SET status = 'cancelled', completed_at = $2
        WHERE id = $1 AND status IN ('pending', 'running')
        """,
        build_id,
        now,
    )
    return result == "UPDATE 1"


# ---------------------------------------------------------------------------
# build_logs
# ---------------------------------------------------------------------------


async def append_build_log(
    build_id: UUID,
    message: str,
    source: str = "builder",
    level: str = "info",
) -> dict:
    """Append a log entry to a build."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO build_logs (build_id, source, level, message)
        VALUES ($1, $2, $3, $4)
        RETURNING id, build_id, timestamp, source, level, message, created_at
        """,
        build_id,
        source,
        level,
        message,
    )
    return dict(row)


async def get_build_logs(
    build_id: UUID,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Fetch paginated build logs and total count."""
    pool = await get_pool()
    count_row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM build_logs WHERE build_id = $1",
        build_id,
    )
    total = count_row["cnt"] if count_row else 0

    rows = await pool.fetch(
        """
        SELECT id, build_id, timestamp, source, level, message, created_at
        FROM build_logs WHERE build_id = $1
        ORDER BY timestamp ASC
        LIMIT $2 OFFSET $3
        """,
        build_id,
        limit,
        offset,
    )
    return [dict(r) for r in rows], total


# ---------------------------------------------------------------------------
# build_costs
# ---------------------------------------------------------------------------


async def record_build_cost(
    build_id: UUID,
    phase: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
    estimated_cost_usd: Decimal,
) -> dict:
    """Record token usage and cost for a build phase."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO build_costs (build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id, build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at
        """,
        build_id,
        phase,
        input_tokens,
        output_tokens,
        model,
        estimated_cost_usd,
    )
    return dict(row)


async def get_build_costs(build_id: UUID) -> list[dict]:
    """Fetch all cost entries for a build."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, build_id, phase, input_tokens, output_tokens, model, estimated_cost_usd, created_at
        FROM build_costs WHERE build_id = $1
        ORDER BY created_at ASC
        """,
        build_id,
    )
    return [dict(r) for r in rows]


async def get_build_cost_summary(build_id: UUID) -> dict:
    """Aggregate cost summary for a build."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COALESCE(SUM(input_tokens), 0)  AS total_input_tokens,
            COALESCE(SUM(output_tokens), 0) AS total_output_tokens,
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost_usd,
            COUNT(*) AS phase_count
        FROM build_costs WHERE build_id = $1
        """,
        build_id,
    )
    return dict(row) if row else {
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "total_cost_usd": Decimal("0"),
        "phase_count": 0,
    }
