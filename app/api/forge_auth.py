"""Auth dependency for Forge read-only endpoints.

Accepts EITHER:
  - Standard JWT bearer token (existing users via web UI)
  - Forge API key (``fg_…`` tokens for CLI / MCP access)

Returns the user dict on success, raises 401 otherwise.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.deps import get_current_user
from app.repos.forge_key_repo import verify_api_key

_bearer_scheme = HTTPBearer(auto_error=False)

_KEY_PREFIX = "fg_"


async def get_forge_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> dict:
    """Authenticate via JWT *or* Forge API key.

    If the token starts with ``fg_`` it is treated as an API key;
    otherwise it falls through to normal JWT validation.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
        )

    token = credentials.credentials

    # ── API key path ──────────────────────────────────────────
    if token.startswith(_KEY_PREFIX):
        result = await verify_api_key(token)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or revoked API key",
            )
        # Attach scopes so routers can check permissions
        result["_scopes"] = result.get("scopes", [])
        return result

    # ── JWT path (delegate to existing dependency) ────────────
    return await get_current_user(credentials)
