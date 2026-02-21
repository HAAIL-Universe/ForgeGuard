"""Database connection pool management.

Provides a resilient asyncpg pool wrapper that transparently retries
operations when the underlying TCP connection has been reset by the
remote host (common on Windows, cloud DBs with idle-connection
reapers, or after PostgreSQL restarts).
"""

import asyncio
import logging
from typing import Any

import asyncpg

from app.config import settings

logger = logging.getLogger(__name__)

# Exceptions that mean "the connection died — retry with a fresh one"
_RETRY_EXCEPTIONS = (
    asyncpg.ConnectionDoesNotExistError,
    asyncpg.InterfaceError,
    ConnectionResetError,
    OSError,
)

_MAX_RETRIES = 4  # 5 total attempts — gives Neon time to cold-start (~5–10 s)


def _invalidate_pool() -> None:
    """Mark the current pool as dead so the next get_pool() call recreates it.

    Called when all retries are exhausted, meaning the pool itself is poisoned
    (e.g. full Neon compute restart). Clearing the module-level references
    ensures the next caller gets a freshly created pool rather than continuing
    to hand out dead connections.
    """
    global _pool, _wrapper
    _pool = None
    _wrapper = None


class _ResilientPool:
    """Thin wrapper around :class:`asyncpg.Pool` that retries on dead connections.

    Only the four common Pool shorthand methods used throughout the codebase
    are wrapped.  All other attribute access is proxied straight through so
    the object is a drop-in replacement for ``asyncpg.Pool``.
    """

    __slots__ = ("_pool",)

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── proxied methods with retry ────────────────────────────

    async def fetch(self, query: str, *args: Any, **kw: Any) -> list:
        return await self._retry(self._pool.fetch, query, *args, **kw)

    async def fetchrow(self, query: str, *args: Any, **kw: Any):
        return await self._retry(self._pool.fetchrow, query, *args, **kw)

    async def fetchval(self, query: str, *args: Any, **kw: Any):
        return await self._retry(self._pool.fetchval, query, *args, **kw)

    async def execute(self, query: str, *args: Any, **kw: Any) -> str:
        return await self._retry(self._pool.execute, query, *args, **kw)

    # ── retry engine ──────────────────────────────────────────

    @staticmethod
    async def _retry(func, *args: Any, **kw: Any):
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return await func(*args, **kw)
            except _RETRY_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    # Exponential backoff: 0.5 s, 1 s, 2 s, 4 s (capped at 10 s)
                    # Gives Neon enough time to complete a cold-start between attempts.
                    wait = min(0.5 * (2 ** attempt), 10.0)
                    logger.warning(
                        "DB connection lost (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, _MAX_RETRIES + 1, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    # All retries exhausted — invalidate the pool so the next
                    # caller gets a freshly created one instead of a dead one.
                    _invalidate_pool()
                    raise
        raise last_exc  # type: ignore[misc]  # unreachable but keeps mypy happy

    # ── transparent proxy for everything else ─────────────────

    def __getattr__(self, name: str):
        return getattr(self._pool, name)


_pool: asyncpg.Pool | None = None
_pool_loop: asyncio.AbstractEventLoop | None = None
_wrapper: _ResilientPool | None = None


async def get_pool() -> _ResilientPool:  # type: ignore[override]
    """Get or create the database connection pool.

    Returns a :class:`_ResilientPool` wrapper that transparently retries
    on dead-connection errors (``ConnectionDoesNotExistError``, etc.).

    If the running event loop has changed (common in test suites),
    the stale pool is discarded and a fresh one is created.
    """
    global _pool, _pool_loop, _wrapper
    loop = asyncio.get_running_loop()
    if _pool is not None and _pool_loop is not loop:
        try:
            _pool.terminate()
        except Exception:
            pass
        _pool = None
        _wrapper = None
    if _pool is None:
        _pool = await asyncio.wait_for(
            asyncpg.create_pool(
                dsn=settings.DATABASE_URL,
                min_size=2,
                max_size=10,
                command_timeout=60,
                max_inactive_connection_lifetime=300.0,    # 5 min — survives long planner runs
                server_settings={
                    "statement_timeout": "30000",                    # 30s max query
                    "idle_in_transaction_session_timeout": "60000",  # 60s
                },
            ),
            timeout=20,  # outer asyncio timeout (Neon cold-start can take ~10s)
        )
        _pool_loop = loop
        _wrapper = _ResilientPool(_pool)
    return _wrapper  # type: ignore[return-value]


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool, _pool_loop, _wrapper
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None
        _wrapper = None
