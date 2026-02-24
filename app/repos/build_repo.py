"""Build repository -- database reads and writes for builds, build_logs, build_costs, and build_errors tables."""

import hashlib
import json
import re
from decimal import Decimal
from datetime import datetime, timezone
from uuid import UUID

from app.repos.db import get_pool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# Common file-extension patterns for extracting paths from error messages
_FILE_RE = re.compile(
    r"""(?:File\s+["']|in\s+|at\s+|from\s+)"""             # prefix
    r"""([\w./\\-]+\.(?:tsx|jsx|yaml|json|toml|yml|py|ts|js|sql|md))""",  # path (longest-first)
    re.IGNORECASE,
)

# Governance-style patterns: "[G3] ... : app/config.py imports ..."
# and "Failed to generate app/services/__init__.py: ..."
_GOV_FILE_RE = re.compile(
    r"""(?::\s*|gate:\s*|generate\s+)"""                    # prefix (colon, gate:, generate)
    r"""([\w./\\-]+\.(?:tsx|jsx|yaml|json|toml|yml|py|ts|js|sql|md))""",  # path
    re.IGNORECASE,
)


def _extract_file_path(message: str) -> str | None:
    """Best-effort file path extraction from an error message."""
    m = _FILE_RE.search(message)
    if m:
        return m.group(1)
    m = _GOV_FILE_RE.search(message)
    return m.group(1) if m else None


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
                  paused_at, pause_reason, pause_phase, completed_phases, base_commit_sha
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
               paused_at, pause_reason, pause_phase, completed_phases, base_commit_sha
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
               paused_at, pause_reason, pause_phase, completed_phases, base_commit_sha
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
               paused_at, pause_reason, pause_phase, completed_phases, base_commit_sha
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


async def delete_zombie_builds_for_project(project_id: UUID) -> int:
    """Delete terminal builds that completed no phases for a given project.

    A "zombie" build is one that reached a terminal state (failed, cancelled,
    completed) without ever completing a phase (completed_phases == -1).
    These are abandoned or immediately-failed builds with no useful progress.

    Returns the count deleted.
    """
    pool = await get_pool()
    result = await pool.execute(
        """
        DELETE FROM builds
        WHERE project_id = $1
          AND completed_phases = -1
          AND status IN ('failed', 'cancelled', 'completed')
        """,
        project_id,
    )
    parts = result.split()
    return int(parts[1]) if len(parts) == 2 else 0


async def delete_all_zombie_builds() -> int:
    """Delete ALL zombie builds across all projects.

    Called at server startup to clean up builds abandoned when the previous
    server instance was killed before they could fail gracefully.

    Returns the total count deleted.
    """
    pool = await get_pool()
    result = await pool.execute(
        """
        DELETE FROM builds
        WHERE completed_phases = -1
          AND status IN ('failed', 'cancelled', 'completed')
        """
    )
    parts = result.split()
    return int(parts[1]) if len(parts) == 2 else 0


async def interrupt_stale_builds() -> int:
    """Handle active builds on server startup.

    Called once during lifespan startup to deal with builds left in an
    active state by a previous server instance (crash, SIGTERM, restart).

    **Gated builds** (those with a ``pending_gate``) are paused instead of
    failed — the gate is preserved so the user can respond after restart and
    the build can resume from where it left off.

    **Non-gated builds** are marked failed (existing behaviour).

    Returns the total number of builds interrupted (paused + failed).
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)

    # 1. Builds WITH a pending gate → pause (preserve gate state)
    gated_result = await pool.execute(
        """
        UPDATE builds
           SET status       = 'paused',
               paused_at    = $1,
               pause_reason = 'Server restart — gate: ' || pending_gate
         WHERE status IN ('pending', 'running', 'planned')
           AND pending_gate IS NOT NULL
        """,
        now,
    )

    # 2. Builds WITHOUT a gate → fail (existing behaviour)
    ungated_result = await pool.execute(
        """
        UPDATE builds
           SET status       = 'failed',
               completed_at = $1,
               error_detail = 'Interrupted by server restart'
         WHERE status IN ('pending', 'running', 'planned')
           AND pending_gate IS NULL
        """,
        now,
    )

    # asyncpg returns "UPDATE N" as a string
    def _count(r: str) -> int:
        try:
            return int(r.split()[-1])
        except (ValueError, IndexError):
            return 0

    return _count(gated_result) + _count(ungated_result)


async def set_build_gate(build_id: UUID, gate_type: str, payload: dict | None = None) -> None:
    """Persist a pending gate so it survives server restarts.

    Called alongside the in-memory ``register_*`` helpers.  The gate type
    should be one of: ``plan_review``, ``phase_review``, ``ide_ready``,
    ``clarification``.
    """
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    await pool.execute(
        """
        UPDATE builds
           SET pending_gate      = $2,
               gate_payload      = $3::jsonb,
               gate_registered_at = $4
         WHERE id = $1
        """,
        build_id,
        gate_type,
        json.dumps(payload) if payload else None,
        now,
    )


async def clear_build_gate(build_id: UUID) -> None:
    """Clear the pending gate after user responds or build ends."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE builds
           SET pending_gate      = NULL,
               gate_payload      = NULL,
               gate_registered_at = NULL
         WHERE id = $1
        """,
        build_id,
    )


async def has_active_builds(project_id: UUID) -> bool:
    """Return True if the project has any pending/running/paused/planned builds."""
    pool = await get_pool()
    return await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM builds WHERE project_id = $1 AND status IN ('pending', 'running', 'paused', 'planned'))",
        project_id,
    )


async def cancel_build(build_id: UUID) -> bool:
    """Cancel an active build. Returns True if updated."""
    pool = await get_pool()
    now = datetime.now(timezone.utc)
    result = await pool.execute(
        """
        UPDATE builds SET status = 'cancelled', completed_at = $2,
               paused_at = NULL, pause_reason = NULL, pause_phase = NULL
        WHERE id = $1 AND status IN ('pending', 'running', 'paused', 'planned')
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


async def update_base_commit_sha(build_id: UUID, sha: str) -> None:
    """Store the base commit SHA captured right after branch setup."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE builds SET base_commit_sha = $2 WHERE id = $1",
        build_id,
        sha,
    )


# ---------------------------------------------------------------------------
# build_logs
# ---------------------------------------------------------------------------


async def append_build_log(
    build_id: UUID,
    message: str,
    source: str = "builder",
    level: str = "info",
    *,
    phase: str | None = None,
) -> dict:
    """Append a log entry to a build.

    When *level* is ``"error"``, also upserts a row in ``build_errors``
    so the Errors-tab UI can aggregate them with deduplication.
    """
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

    # Auto-track errors in the build_errors table
    if level == "error":
        try:
            await upsert_build_error(
                build_id,
                message,
                source=source,
                severity="error",
                phase=phase,
                file_path=_extract_file_path(message),
            )
        except Exception:
            pass  # Never let error tracking break log persistence

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
        escaped = search.replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
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
          AND (message LIKE 'Build started%' OR message LIKE 'Context compacted%')
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


# ---------------------------------------------------------------------------
# build_errors
# ---------------------------------------------------------------------------


def _error_fingerprint(source: str, severity: str, message: str) -> str:
    """Stable fingerprint for deduplication.

    Strips line numbers, memory addresses, and UUIDs so repeated identical
    errors collapse into a single row with an incremented occurrence_count.
    """
    normalized = re.sub(r"line \d+", "line N", message)
    normalized = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", normalized)
    normalized = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "UUID",
        normalized,
    )
    raw = f"{source}:{severity}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def upsert_build_error(
    build_id: UUID,
    message: str,
    *,
    source: str = "build_log",
    severity: str = "error",
    phase: str | None = None,
    file_path: str | None = None,
) -> dict:
    """Insert a new build error or increment occurrence_count if fingerprint matches.

    Returns the full error record (inserted or updated).
    """
    pool = await get_pool()
    fp = _error_fingerprint(source, severity, message)

    # Try to bump an existing duplicate first
    existing = await pool.fetchrow(
        """
        UPDATE build_errors
        SET occurrence_count = occurrence_count + 1,
            last_seen = now()
        WHERE build_id = $1 AND fingerprint = $2
        RETURNING id, build_id, fingerprint, first_seen, last_seen,
                  occurrence_count, phase, file_path, source, severity,
                  message, resolved, resolved_at, resolution_method,
                  resolution_summary, created_at
        """,
        build_id,
        fp,
    )
    if existing:
        return dict(existing)

    # No duplicate — insert fresh
    row = await pool.fetchrow(
        """
        INSERT INTO build_errors
            (build_id, fingerprint, phase, file_path, source, severity, message)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING id, build_id, fingerprint, first_seen, last_seen,
                  occurrence_count, phase, file_path, source, severity,
                  message, resolved, resolved_at, resolution_method,
                  resolution_summary, created_at
        """,
        build_id,
        fp,
        phase,
        file_path,
        source,
        severity,
        message,
    )
    return dict(row)


async def resolve_build_error(
    error_id: UUID,
    method: str,
    summary: str | None = None,
) -> dict | None:
    """Mark a single build error as resolved.

    method must be one of: 'auto-fix', 'phase-complete', 'dismissed'.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE build_errors
        SET resolved = true,
            resolved_at = now(),
            resolution_method = $2,
            resolution_summary = $3
        WHERE id = $1
        RETURNING id, build_id, fingerprint, first_seen, last_seen,
                  occurrence_count, phase, file_path, source, severity,
                  message, resolved, resolved_at, resolution_method,
                  resolution_summary, created_at
        """,
        error_id,
        method,
        summary,
    )
    return dict(row) if row else None


async def resolve_errors_for_phase(
    build_id: UUID,
    phase: str,
    *,
    method: str = "phase-complete",
    summary: str | None = None,
) -> list[dict]:
    """Bulk-resolve all unresolved errors for a given phase.

    Returns the list of resolved error records.
    """
    pool = await get_pool()
    if summary is None:
        summary = f"{phase} completed — errors cleared"
    rows = await pool.fetch(
        """
        UPDATE build_errors
        SET resolved = true,
            resolved_at = now(),
            resolution_method = $3,
            resolution_summary = $4
        WHERE build_id = $1 AND phase = $2 AND resolved = false
        RETURNING id, build_id, fingerprint, first_seen, last_seen,
                  occurrence_count, phase, file_path, source, severity,
                  message, resolved, resolved_at, resolution_method,
                  resolution_summary, created_at
        """,
        build_id,
        phase,
        method,
        summary,
    )
    return [dict(r) for r in rows]


async def get_build_errors(
    build_id: UUID,
    *,
    resolved_filter: bool | None = None,
) -> list[dict]:
    """Fetch build errors — unresolved first, then resolved.

    If resolved_filter is specified, only return errors matching that status.
    """
    pool = await get_pool()
    where = "build_id = $1"
    params: list = [build_id]
    idx = 2

    if resolved_filter is not None:
        where += f" AND resolved = ${idx}"
        params.append(resolved_filter)
        idx += 1

    rows = await pool.fetch(
        f"""
        SELECT id, build_id, fingerprint, first_seen, last_seen,
               occurrence_count, phase, file_path, source, severity,
               message, resolved, resolved_at, resolution_method,
               resolution_summary, created_at
        FROM build_errors
        WHERE {where}
        ORDER BY resolved ASC, first_seen DESC
        """,
        *params,
    )
    return [dict(r) for r in rows]
