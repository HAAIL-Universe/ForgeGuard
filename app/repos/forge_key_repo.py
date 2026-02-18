"""Forge API key repository -- CRUD for CLI/MCP access tokens."""

import hashlib
import secrets
from uuid import UUID

from app.repos.db import get_pool

_KEY_PREFIX = "fg_"


def _hash_key(raw_key: str) -> str:
    """SHA-256 hash of the raw API key (we never store the plaintext)."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def create_api_key(
    user_id: UUID,
    label: str = "default",
    scopes: list[str] | None = None,
) -> tuple[str, dict]:
    """Generate a new API key for *user_id*.

    Returns ``(raw_key, row_dict)`` — the raw key is shown **once** and
    never stored.
    """
    scopes = scopes or ["read:contracts"]
    raw = _KEY_PREFIX + secrets.token_urlsafe(40)
    prefix = raw[:12]
    key_hash = _hash_key(raw)

    pool = await get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO forge_api_keys (user_id, key_hash, prefix, label, scopes)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id, user_id, prefix, label, scopes, created_at
        """,
        user_id,
        key_hash,
        prefix,
        label,
        scopes,
    )
    return raw, dict(row)


async def verify_api_key(raw_key: str) -> dict | None:
    """Look up a raw API key, returning the joined user row or None.

    Also bumps ``last_used`` on the key row.
    """
    key_hash = _hash_key(raw_key)
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT k.id AS key_id, k.user_id, k.scopes, k.label,
               u.id, u.github_id, u.github_login, u.avatar_url,
               u.access_token, u.anthropic_api_key,
               u.anthropic_api_key_2, u.audit_llm_enabled,
               u.build_spend_cap
        FROM forge_api_keys k
        JOIN users u ON u.id = k.user_id
        WHERE k.key_hash = $1
          AND k.revoked_at IS NULL
        """,
        key_hash,
    )
    if row is None:
        return None

    # Fire-and-forget last_used update (non-blocking)
    await pool.execute(
        "UPDATE forge_api_keys SET last_used = now() WHERE key_hash = $1",
        key_hash,
    )

    return dict(row)


async def list_api_keys(user_id: UUID) -> list[dict]:
    """Return all (non-revoked) API keys for a user (prefix only, no hash)."""
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT id, prefix, label, scopes, created_at, last_used
        FROM forge_api_keys
        WHERE user_id = $1 AND revoked_at IS NULL
        ORDER BY created_at DESC
        """,
        user_id,
    )
    return [dict(r) for r in rows]


async def revoke_api_key(user_id: UUID, key_id: UUID) -> bool:
    """Soft-revoke an API key. Returns True if a row was updated."""
    pool = await get_pool()
    result = await pool.execute(
        """
        UPDATE forge_api_keys
        SET revoked_at = now()
        WHERE id = $1 AND user_id = $2 AND revoked_at IS NULL
        """,
        key_id,
        user_id,
    )
    return result == "UPDATE 1"
