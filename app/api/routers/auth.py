"""Authentication router -- GitHub OAuth flow, user info, and BYOK API key management."""

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.clients.github_client import GITHUB_OAUTH_URL
from app.config import settings
from app.repos.user_repo import set_anthropic_api_key, set_audit_llm_enabled
from app.services.auth_service import handle_github_callback

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory state store for CSRF protection (sufficient for single-process MVP)
_oauth_states: set[str] = set()


@router.get("/github")
async def github_oauth_redirect() -> dict:
    """Return the GitHub OAuth authorization URL with CSRF state."""
    state = secrets.token_urlsafe(32)
    _oauth_states.add(state)

    params = urlencode({
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.FRONTEND_URL}/auth/callback",
        "scope": "read:user repo",
        "state": state,
    })

    return {"redirect_url": f"{GITHUB_OAUTH_URL}?{params}"}


@router.get("/github/callback")
async def github_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> dict:
    """Handle GitHub OAuth callback -- exchange code for JWT."""
    if state not in _oauth_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state",
        )
    _oauth_states.discard(state)

    try:
        result = await handle_github_callback(code)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to authenticate with GitHub",
        )

    return result


@router.get("/me")
async def get_current_user_info(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the current authenticated user info."""
    has_api_key = bool(current_user.get("anthropic_api_key"))
    return {
        "id": str(current_user["id"]),
        "github_login": current_user["github_login"],
        "avatar_url": current_user.get("avatar_url"),
        "has_anthropic_key": has_api_key,
        "audit_llm_enabled": current_user.get("audit_llm_enabled", True),
    }


class ApiKeyBody(BaseModel):
    api_key: str


@router.put("/api-key")
async def save_api_key(
    body: ApiKeyBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save the user's Anthropic API key for BYOK builds."""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    await set_anthropic_api_key(current_user["id"], key)
    return {"saved": True}


@router.delete("/api-key")
async def remove_api_key(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove the user's stored Anthropic API key."""
    await set_anthropic_api_key(current_user["id"], None)
    return {"removed": True}


class AuditToggleBody(BaseModel):
    enabled: bool


@router.put("/audit-toggle")
async def toggle_audit_llm(
    body: AuditToggleBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Enable or disable the LLM auditor for builds."""
    await set_audit_llm_enabled(current_user["id"], body.enabled)
    return {"audit_llm_enabled": body.enabled}
