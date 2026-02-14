"""User repository -- database reads and writes for the users table."""

from uuid import UUID

import asyncpg

from app.repos.db import get_pool


async def upsert_user(
    github_id: int,
    github_login: str,
    avatar_url: str | None,
    access_token: str,
) -> dict:
    """Create or update a user by github_id. Returns the user row as a dict."""
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO users (github_id, github_login, avatar_url, access_token, updated_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (github_id) DO UPDATE SET
            github_login = EXCLUDED.github_login,
            avatar_url = EXCLUDED.avatar_url,
            access_token = EXCLUDED.access_token,
            updated_at = now()
        RETURNING id, github_id, github_login, avatar_url, created_at, updated_at
        """,
        github_id,
        github_login,
        avatar_url,
        access_token,
    )
    return dict(row)


async def get_user_by_id(user_id: UUID) -> dict | None:
    """Fetch a user by primary key. Returns None if not found."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT id, github_id, github_login, avatar_url, created_at, updated_at FROM users WHERE id = $1",
        user_id,
    )
    return dict(row) if row else None
