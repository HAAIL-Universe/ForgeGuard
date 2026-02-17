"""Project repository -- database reads and writes for projects and project_contracts tables."""

import json
from uuid import UUID

from app.repos.db import get_pool


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


async def create_project(
    user_id: UUID,
    name: str,
    description: str | None = None,
    repo_id: UUID | None = None,
    local_path: str | None = None,
) -> dict:
    """Insert a new project. Returns the created row as a dict."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO projects (user_id, name, description, repo_id, local_path)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, user_id, name, description, status, repo_id,
                  local_path, questionnaire_state, created_at, updated_at
        """,
        user_id,
        name,
        description,
        repo_id,
        local_path,
    )
    return _project_to_dict(row)


async def get_project_by_id(project_id: UUID) -> dict | None:
    """Fetch a project by primary key. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT p.id, p.user_id, p.name, p.description, p.status, p.repo_id,
               p.local_path, p.questionnaire_state, p.created_at, p.updated_at,
               r.full_name AS repo_full_name
        FROM projects p
        LEFT JOIN repos r ON r.id = p.repo_id
        WHERE p.id = $1
        """,
        project_id,
    )
    return _project_to_dict(row) if row else None


async def get_projects_by_user(user_id: UUID) -> list[dict]:
    """Fetch all projects for a user, newest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, user_id, name, description, status, repo_id,
               local_path, questionnaire_state, created_at, updated_at
        FROM projects
        WHERE user_id = $1
        ORDER BY created_at DESC
        """,
        user_id,
    )
    return [_project_to_dict(r) for r in rows]


async def update_project_status(project_id: UUID, status: str) -> None:
    """Update the status of a project."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE projects SET status = $2, updated_at = now()
        WHERE id = $1
        """,
        project_id,
        status,
    )


async def update_questionnaire_state(
    project_id: UUID,
    state: dict,
) -> None:
    """Overwrite the questionnaire_state JSONB column."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE projects SET questionnaire_state = $2::jsonb, updated_at = now()
        WHERE id = $1
        """,
        project_id,
        json.dumps(state),
    )


async def delete_project(project_id: UUID) -> bool:
    """Delete a project by primary key. Returns True if a row was deleted."""
    pool = await get_pool()
    result = await pool.execute(
        "DELETE FROM projects WHERE id = $1",
        project_id,
    )
    return result == "DELETE 1"


# ---------------------------------------------------------------------------
# project_contracts
# ---------------------------------------------------------------------------


async def upsert_contract(
    project_id: UUID,
    contract_type: str,
    content: str,
    version: int = 1,
) -> dict:
    """Insert or update a contract for a project. Returns the row as a dict."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO project_contracts (project_id, contract_type, content, version)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (project_id, contract_type)
        DO UPDATE SET content = EXCLUDED.content,
                      version = project_contracts.version + 1,
                      updated_at = now()
        RETURNING id, project_id, contract_type, content, version,
                  created_at, updated_at
        """,
        project_id,
        contract_type,
        content,
        version,
    )
    return dict(row)


async def get_contracts_by_project(project_id: UUID) -> list[dict]:
    """Fetch all contracts for a project."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, project_id, contract_type, content, version,
               created_at, updated_at
        FROM project_contracts
        WHERE project_id = $1
        ORDER BY contract_type
        """,
        project_id,
    )
    return [dict(r) for r in rows]


async def get_contract_by_type(
    project_id: UUID,
    contract_type: str,
) -> dict | None:
    """Fetch a single contract by project and type. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, project_id, contract_type, content, version,
               created_at, updated_at
        FROM project_contracts
        WHERE project_id = $1 AND contract_type = $2
        """,
        project_id,
        contract_type,
    )
    return dict(row) if row else None


async def update_contract_content(
    project_id: UUID,
    contract_type: str,
    content: str,
) -> dict | None:
    """Update the content of an existing contract. Returns updated row or None."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        UPDATE project_contracts
        SET content = $3, version = version + 1, updated_at = now()
        WHERE project_id = $1 AND contract_type = $2
        RETURNING id, project_id, contract_type, content, version,
                  created_at, updated_at
        """,
        project_id,
        contract_type,
        content,
    )
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# contract_snapshots
# ---------------------------------------------------------------------------


async def snapshot_contracts(project_id: UUID) -> int | None:
    """Copy all current project_contracts into contract_snapshots as a new batch.

    Returns the new batch number, or None if there were no contracts to snapshot.
    Uses a transaction to prevent concurrent calls from picking the same batch number.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Determine the next batch number
            max_batch = await conn.fetchval(
                "SELECT COALESCE(MAX(batch), 0) FROM contract_snapshots WHERE project_id = $1",
                project_id,
            )
            new_batch = max_batch + 1

            # Copy current contracts into snapshots
            inserted = await conn.execute(
                """
                INSERT INTO contract_snapshots (project_id, batch, contract_type, content)
                SELECT project_id, $2, contract_type, content
                FROM project_contracts
                WHERE project_id = $1
                """,
                project_id,
                new_batch,
            )
    # inserted is like 'INSERT 0 9' — extract count
    count = int(inserted.split()[-1])
    return new_batch if count > 0 else None


async def get_snapshot_batches(project_id: UUID) -> list[dict]:
    """Return a summary of all snapshot batches for a project.

    Each entry: {batch, created_at, count}.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT batch, MIN(created_at) AS created_at, COUNT(*) AS count
        FROM contract_snapshots
        WHERE project_id = $1
        GROUP BY batch
        ORDER BY batch DESC
        """,
        project_id,
    )
    return [dict(r) for r in rows]


async def get_snapshot_contracts(project_id: UUID, batch: int) -> list[dict]:
    """Return all contracts for a specific snapshot batch."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, project_id, batch, contract_type, content, created_at
        FROM contract_snapshots
        WHERE project_id = $1 AND batch = $2
        ORDER BY contract_type
        """,
        project_id,
        batch,
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _project_to_dict(row) -> dict:
    """Convert a project row to a dict, parsing JSONB questionnaire_state."""
    d = dict(row)
    qs = d.get("questionnaire_state")
    if isinstance(qs, str):
        d["questionnaire_state"] = json.loads(qs)
    return d
