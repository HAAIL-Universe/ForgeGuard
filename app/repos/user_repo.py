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
        "SELECT id, github_id, github_login, avatar_url, access_token, anthropic_api_key, anthropic_api_key_2, audit_llm_enabled, created_at, updated_at FROM users WHERE id = $1",
        user_id,
    )
    return dict(row) if row else None


async def set_anthropic_api_key(user_id: UUID, api_key: str | None) -> None:
    """Store (or clear) the user's Anthropic API key."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET anthropic_api_key = $2, updated_at = now() WHERE id = $1",
        user_id,
        api_key,
    )


async def set_anthropic_api_key_2(user_id: UUID, api_key: str | None) -> None:
    """Store (or clear) the user's second Anthropic API key."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET anthropic_api_key_2 = $2, updated_at = now() WHERE id = $1",
        user_id,
        api_key,
    )


async def set_audit_llm_enabled(user_id: UUID, enabled: bool) -> None:
    """Toggle the LLM auditor for builds."""
    pool = await get_pool()
    await pool.execute(
        "UPDATE users SET audit_llm_enabled = $2, updated_at = now() WHERE id = $1",
        user_id,
        enabled,
    )
