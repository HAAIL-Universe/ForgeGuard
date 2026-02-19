"""Certificate repository -- persist and retrieve Forge Seal records."""

import json
from uuid import UUID

from app.repos.db import get_pool


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


async def create_certificate(
    project_id: UUID,
    build_id: UUID,
    user_id: UUID,
    scores: dict,
    certificate_html: str,
    build_cycle_id: UUID | None = None,
    integrity_hash: str | None = None,
) -> dict:
    """Persist a forge seal. Returns the saved row as a dict."""
    verdict = scores.get("verdict", "FLAGGED")
    overall_score = scores.get("overall_score", 0)
    baseline_score = scores.get("baseline_score")
    delta_json = scores.get("delta")

    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO certificates (
            project_id, build_id, user_id, build_cycle_id,
            verdict, overall_score, baseline_score,
            scores_json, delta_json, certificate_html, integrity_hash
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11)
        RETURNING *
        """,
        project_id,
        build_id,
        user_id,
        build_cycle_id,
        verdict,
        overall_score,
        baseline_score,
        json.dumps(scores),
        json.dumps(delta_json) if delta_json is not None else None,
        certificate_html,
        integrity_hash,
    )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


async def get_certificate_by_build(build_id: UUID) -> dict | None:
    """Fetch the certificate for a specific build. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM certificates WHERE build_id = $1
        ORDER BY created_at DESC LIMIT 1
        """,
        build_id,
    )
    return _row_to_dict(row) if row else None


async def get_latest_certificate(project_id: UUID) -> dict | None:
    """Fetch the most recent certificate for a project. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT * FROM certificates
        WHERE project_id = $1
        ORDER BY created_at DESC
        LIMIT 1
        """,
        project_id,
    )
    return _row_to_dict(row) if row else None


async def get_certificates_by_project(
    project_id: UUID, limit: int = 10
) -> list[dict]:
    """Fetch recent certificates for a project, newest first."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM certificates
        WHERE project_id = $1
        ORDER BY created_at DESC
        LIMIT $2
        """,
        project_id,
        limit,
    )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _row_to_dict(row) -> dict:
    """Convert a certificate row to a dict, deserialising JSONB columns."""
    d = dict(row)
    for col in ("scores_json", "delta_json"):
        val = d.get(col)
        if isinstance(val, str):
            d[col] = json.loads(val)
    return d
