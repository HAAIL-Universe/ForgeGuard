"""Remote-mode HTTP client — proxy MCP calls to the ForgeGuard API."""

from __future__ import annotations

from typing import Any

from .cache import cache_get, cache_set
from .config import FORGEGUARD_API_KEY, FORGEGUARD_URL

_http_client = None


def _get_client():
    global _http_client
    if _http_client is None:
        import httpx

        headers = {"User-Agent": "ForgeGuard-MCP/1.0"}
        if FORGEGUARD_API_KEY:
            headers["Authorization"] = f"Bearer {FORGEGUARD_API_KEY}"
        _http_client = httpx.AsyncClient(
            base_url=FORGEGUARD_URL,
            headers=headers,
            timeout=30.0,
        )
    return _http_client


async def api_get(path: str) -> dict[str, Any]:
    """GET a ForgeGuard endpoint, returning parsed JSON (cached)."""
    cached = cache_get(f"api:{path}")
    if cached is not None:
        return cached

    client = _get_client()
    resp = await client.get(path)
    resp.raise_for_status()
    return cache_set(f"api:{path}", resp.json())
