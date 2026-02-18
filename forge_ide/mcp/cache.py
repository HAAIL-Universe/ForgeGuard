"""Simple TTL cache for MCP server responses."""

from __future__ import annotations

import time
from typing import Any

_CACHE_TTL = 300  # 5 minutes
_cache: dict[str, tuple[float, Any]] = {}


def cache_get(key: str) -> Any | None:
    """Return cached value if fresh, else None."""
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return entry[1]
    return None


def cache_set(key: str, value: Any) -> Any:
    """Store value in cache and return it."""
    _cache[key] = (time.monotonic(), value)
    return value


def cache_clear() -> None:
    """Clear all cached entries."""
    _cache.clear()
