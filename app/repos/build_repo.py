"""Build repository -- database reads and writes for builds, build_logs, and build_costs tables."""

import json
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID

from app.repos.db import get_pool


# ---------------------------------------------------------------------------
# builds
# ---------------------------------------------------------------------------


async def create_build(
    project_id: UUID,
    *,
    target_type: str | None = None,
    target_ref: str | None = None,
    working_dir: str | None = None,
    branch: str = "main",
    build_mode: str = "plan_execute",
    contract_batch: int | None = None,
) -> dict:
    """Create a new build record in pending status."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO builds (project_id, phase, status, target_type, target_ref, working_dir, branch, build_mode, contract_batch)
        VALUES ($1, 'Phase 0', 'pending', $2, $3, $4, $5, $6, $7)
        RETURNING id, project_id, phase, status, target_type, target_ref,
                  working_dir, branch, build_mode, contract_batch, started_at, completed_at,
                  loop_count, error_detail, created_at,
                  paused_at, pause_reason, pause_phase, completed_phases
        """,
        project_id,
        target_type,
        target_ref,
        working_dir,
        branch,
        build_mode,
        contract_batch,
    )
    return dict(row)


async def get_build_by_id(build_id: UUID) -> dict | None:
    """Fetch a single build by ID."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, project_id, phase, status, target_type, target_ref,
               working_dir, branch, build_mode, contract_batch, started_at, completed_at,
               loop_count, error_detail, created_at,
               paused_at, pause_reason, pause_phase, completed_phases
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
        SELECT id, project_id, phase, status, target_type, target_ref,
               working_dir, branch, build_mode, contract_batch, started_at, completed_at,
               loop_count, error_detail, created_at,
               paused_at, pause_reason, pause_phase, completed_phases
        FROM builds WHERE project_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        project_id,
    )
    return dict(row) if row else None


async def get_builds_for_project(project_id: UUID) -> list[dict]:
    """Fetch all builds for a project, newest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, project_id, phase, status, target_type, target_ref,
               working_dir, branch, build_mode, contract_batch, started_at, completed_at,
               loop_count, error_detail, created_at,
               paused_at, pause_reason, pause_phase, completed_phases
        FROM builds WHERE project_id = $1
        ORDER BY created_at DESC
        """,
        project_id,
    )
    return [dict(r) for r in rows]


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


async def delete_builds(build_ids: list[UUID]) -> int:
    """Delete builds by ID. Returns count of deleted rows.

    Child rows (build_logs, build_costs) are removed via ON DELETE CASCADE.
    """
    if not build_ids:
        return 0
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM builds WHERE id = ANY($1::uuid[])",
        build_ids,
    )
    # result is e.g. "DELETE 3"
    parts = result.split()
    return int(parts[1]) if len(parts) == 2 else 0


async def cancel_build(build_id: UUID) -> bool:
    """Cancel an active build. Returns True if updated."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    result = await pool.execute(
        """
        UPDATE builds SET status = 'cancelled', completed_at = $2,
               paused_at = NULL, pause_reason = NULL, pause_phase = NULL
        WHERE id = $1 AND status IN ('pending', 'running', 'paused')
        """,
        build_id,
        now,
    )
    return result == "UPDATE 1"


async def pause_build(
    build_id: UUID,
    reason: str,
    phase: str,
) -> bool:
    """Pause a running build. Returns True if updated."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    result = await pool.execute(
        """
        UPDATE builds SET status = 'paused', paused_at = $2,
               pause_reason = $3, pause_phase = $4
        WHERE id = $1 AND status = 'running'
        """,
        build_id,
        now,
        reason,
        phase,
    )
    return result == "UPDATE 1"


async def update_completed_phases(build_id: UUID, phase_num: int) -> None:
    """Record the highest completed phase number for build continuation."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE builds SET completed_phases = $2 WHERE id = $1",
        build_id,
        phase_num,
    )


async def resume_build(build_id: UUID) -> bool:
    """Resume a paused build. Returns True if updated."""
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE builds SET status = 'running',
               paused_at = NULL, pause_reason = NULL, pause_phase = NULL
        WHERE id = $1 AND status = 'paused'
        """,
        build_id,
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
    *,
    search: str | None = None,
    level: str | None = None,
) -> tuple[list[dict], int]:
    """Fetch paginated build logs with optional search and level filter."""
    pool = await get_pool()

    where = "build_id = $1"
    params: list = [build_id]
    idx = 2

    if search:
        where += f" AND message ILIKE ${idx}"
        params.append(f"%{search}%")
        idx += 1
    if level:
        where += f" AND level = ${idx}"
        params.append(level)
        idx += 1

    count_row = await pool.fetchrow(
        f"SELECT COUNT(*) AS cnt FROM build_logs WHERE {where}",
        *params,
    )
    total = count_row["cnt"] if count_row else 0

    rows = await pool.fetch(
        f"""
        SELECT id, build_id, timestamp, source, level, message, created_at
        FROM build_logs WHERE {where}
        ORDER BY timestamp ASC
        LIMIT ${idx} OFFSET ${idx + 1}
        """,
        *params,
        limit,
        offset,
    )
    return [dict(r) for r in rows], total


async def get_build_stats(build_id: UUID) -> dict:
    """Aggregate observability stats for a build.

    Returns total_turns, total_audit_attempts, files_written_count,
    git_commits_made, interjections_received.
    """
    pool = await get_pool()

    # Count turns (builder log entries = one per streamed chunk, but
    # we approximate by counting distinct builder turns via system log messages)
    turns_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM build_logs
        WHERE build_id = $1 AND source = 'system'
          AND message LIKE 'Build started%'
           OR (build_id = $1 AND source = 'system' AND message LIKE 'Context compacted%')
        """,
        build_id,
    )

    audit_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM build_logs
        WHERE build_id = $1 AND source = 'audit'
          AND (message LIKE 'Audit PASS%' OR message LIKE 'Audit FAIL%'
               OR message LIKE 'Auditor report%')
        """,
        build_id,
    )

    files_row = await pool.fetchrow(
        "SELECT COUNT(*) AS cnt FROM build_logs WHERE build_id = $1 AND source = 'file'",
        build_id,
    )

    commits_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM build_logs
        WHERE build_id = $1 AND source = 'system'
          AND (message LIKE 'Committed%' OR message LIKE 'Final commit%')
        """,
        build_id,
    )

    interject_row = await pool.fetchrow(
        """
        SELECT COUNT(*) AS cnt FROM build_logs
        WHERE build_id = $1 AND source = 'user'
          AND message LIKE 'User interjection%'
        """,
        build_id,
    )

    return {
        "total_turns": (turns_row["cnt"] if turns_row else 0),
        "total_audit_attempts": (audit_row["cnt"] if audit_row else 0),
        "files_written_count": (files_row["cnt"] if files_row else 0),
        "git_commits_made": (commits_row["cnt"] if commits_row else 0),
        "interjections_received": (interject_row["cnt"] if interject_row else 0),
    }


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


async def get_build_file_logs(build_id: UUID) -> list[dict]:
    """Fetch all file_created log entries for a build.

    Returns list of dicts with path, size_bytes, language, created_at
    parsed from build_log messages where source='file'.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT message, created_at
        FROM build_logs
        WHERE build_id = $1 AND source = 'file'
        ORDER BY created_at ASC
        """,
        build_id,
    )
    files = []
    for r in rows:
        try:
            data = json.loads(r["message"])
            files.append({
                "path": data.get("path", ""),
                "size_bytes": data.get("size_bytes", 0),
                "language": data.get("language", ""),
                "created_at": r["created_at"],
            })
        except (ValueError, KeyError):
            continue
    return files
