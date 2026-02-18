"""Database connection pool management."""

import asyncio

import asyncpg

from app.config import settings

_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the database connection pool.

    If the running event loop has changed (common in test suites),
    the stale pool is discarded and a fresh one is created.
    """
    global _pool, _pool_loop
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is not loop:
        try:
            _pool.terminate()
        except Exception:
            pass
        _pool = None
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=2,
            max_size=10,
            command_timeout=60,                       # asyncpg-level timeout per query
            max_inactive_connection_lifetime=120.0,    # expire idle conns after 2 min
            server_settings={
                "statement_timeout": "30000",                    # 30s max query
                "idle_in_transaction_session_timeout": "60000",  # 60s
            },
        )
        _pool_loop = loop
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None
