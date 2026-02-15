"""Audit repository -- database reads and writes for audit_runs and audit_checks."""

from uuid import UUID

from app.repos.db import get_pool


async def create_audit_run(
    repo_id: UUID,
    commit_sha: str,
    commit_message: str | None,
    commit_author: str | None,
    branch: str | None,
) -> dict:
    """Insert a new audit run with status 'pending'. Returns the row as a dict."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO audit_runs (repo_id, commit_sha, commit_message, commit_author, branch, status)
        VALUES ($1, $2, $3, $4, $5, 'pending')
        RETURNING id, repo_id, commit_sha, commit_message, commit_author, branch,
                  status, overall_result, started_at, completed_at, files_checked, created_at
        """,
        repo_id,
        commit_sha,
        commit_message,
        commit_author,
        branch,
    )
    return dict(row)


async def get_existing_commit_shas(repo_id: UUID) -> set[str]:
    """Return the set of commit SHAs already recorded for a repo."""
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT commit_sha FROM audit_runs WHERE repo_id = $1",
        repo_id,
    )
    return {r["commit_sha"] for r in rows}


async def update_audit_run(
    audit_run_id: UUID,
    status: str,
    overall_result: str | None,
    files_checked: int,
) -> None:
    """Update an audit run with results."""
    pool = await get_pool()
    completed = "now()" if status in ("completed", "error") else "NULL"
    await pool.execute(
        f"""
        UPDATE audit_runs
        SET status = $2, overall_result = $3, files_checked = $4,
            completed_at = {completed}
        WHERE id = $1
        """,
        audit_run_id,
        status,
        overall_result,
        files_checked,
    )


async def mark_stale_audit_runs(repo_id: UUID, stale_minutes: int = 5) -> int:
    """Mark audit runs stuck in 'pending' or 'running' for longer than
    *stale_minutes* as 'error'. Returns the number of rows updated."""
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE audit_runs
        SET status = 'error', overall_result = 'ERROR',
            completed_at = now()
        WHERE repo_id = $1
          AND status IN ('pending', 'running')
          AND created_at < now() - ($2 || ' minutes')::interval
        """,
        repo_id,
        str(stale_minutes),
    )
    # asyncpg returns 'UPDATE N'
    return int(result.split()[-1])


async def insert_audit_checks(
    audit_run_id: UUID,
    checks: list[dict],
) -> None:
    """Insert multiple audit check results for an audit run."""
    if not checks:
        return
    pool = await get_pool()
    await pool.executemany(
        """
        INSERT INTO audit_checks (audit_run_id, check_code, check_name, result, detail)
        VALUES ($1, $2, $3, $4, $5)
        """,
        [
            (
                audit_run_id,
                c["check_code"],
                c["check_name"],
                c["result"],
                c.get("detail"),
            )
            for c in checks
        ],
    )


async def get_audit_runs_by_repo(
    repo_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Fetch audit runs for a repo, newest first. Returns (items, total)."""
    pool = await get_pool()

    total_row = await pool.fetchrow(
        "SELECT count(*) as cnt FROM audit_runs WHERE repo_id = $1",
        repo_id,
    )
    total = total_row["cnt"] if total_row else 0

    rows = await pool.fetch(
        """
        SELECT a.id, a.repo_id, a.commit_sha, a.commit_message, a.commit_author, a.branch,
               a.status, a.overall_result, a.started_at, a.completed_at, a.files_checked, a.created_at,
               cs.check_summary
        FROM audit_runs a
        LEFT JOIN LATERAL (
            SELECT string_agg(c.check_code || ':' || c.result, ' ' ORDER BY c.created_at) AS check_summary
            FROM audit_checks c
            WHERE c.audit_run_id = a.id
        ) cs ON true
        WHERE a.repo_id = $1
        ORDER BY a.created_at DESC
        LIMIT $2 OFFSET $3
        """,
        repo_id,
        limit,
        offset,
    )
    return [dict(r) for r in rows], total


async def get_audit_run_detail(audit_run_id: UUID) -> dict | None:
    """Fetch a single audit run with its checks."""
    pool = await get_pool()
    run_row = await pool.fetchrow(
        """
        SELECT id, repo_id, commit_sha, commit_message, commit_author, branch,
               status, overall_result, started_at, completed_at, files_checked, created_at
        FROM audit_runs
        WHERE id = $1
        """,
        audit_run_id,
    )
    if not run_row:
        return None

    check_rows = await pool.fetch(
        """
        SELECT id, check_code, check_name, result, detail, created_at
        FROM audit_checks
        WHERE audit_run_id = $1
        ORDER BY created_at ASC
        """,
        audit_run_id,
    )

    result = dict(run_row)
    result["checks"] = [dict(c) for c in check_rows]
    return result
