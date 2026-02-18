"""Authentication router -- GitHub OAuth flow, user info, and BYOK API key management."""

import secrets
import time
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import get_current_user
from app.clients.github_client import GITHUB_OAUTH_URL
from app.config import settings
from app.repos.user_repo import set_anthropic_api_key, set_anthropic_api_key_2, set_audit_llm_enabled, set_build_spend_cap
from app.services.auth_service import handle_github_callback

router = APIRouter(prefix="/auth", tags=["auth"])

# In-memory state store for CSRF protection (sufficient for single-process MVP).
# TTL-capped to prevent unbounded growth from abandoned OAuth flows.
_OAUTH_STATE_TTL = 600  # 10 minutes
_OAUTH_STATE_MAX = 10_000
_oauth_states: dict[str, float] = {}  # state_token -> creation_time


def _prune_oauth_states() -> None:
    """Remove expired OAuth state tokens."""
    cutoff = time.monotonic() - _OAUTH_STATE_TTL
    expired = [k for k, v in _oauth_states.items() if v < cutoff]
    for k in expired:
        _oauth_states.pop(k, None)


@router.get("/github")
async def github_oauth_redirect() -> dict:
    """Return the GitHub OAuth authorization URL with CSRF state."""
    _prune_oauth_states()
    if len(_oauth_states) >= _OAUTH_STATE_MAX:
        raise HTTPException(status_code=503, detail="Too many pending OAuth flows")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = time.monotonic()

    params = urlencode({
        "client_id": settings.GITHUB_CLIENT_ID,
        "redirect_uri": f"{settings.FRONTEND_URL}/auth/callback",
        "scope": "read:user repo workflow",
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
    _oauth_states.pop(state, None)

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
    has_api_key_2 = bool(current_user.get("anthropic_api_key_2"))
    raw_cap = current_user.get("build_spend_cap")
    spend_cap = float(raw_cap) if raw_cap is not None else None
    return {
        "id": str(current_user["id"]),
        "github_login": current_user["github_login"],
        "avatar_url": current_user.get("avatar_url"),
        "has_anthropic_key": has_api_key,
        "has_anthropic_key_2": has_api_key_2,
        "audit_llm_enabled": current_user.get("audit_llm_enabled", True),
        "build_spend_cap": spend_cap,
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


@router.put("/api-key-2")
async def save_api_key_2(
    body: ApiKeyBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Save the user's second Anthropic API key for key-pool throughput."""
    key = body.api_key.strip()
    if not key:
        raise HTTPException(status_code=400, detail="API key cannot be empty")
    await set_anthropic_api_key_2(current_user["id"], key)
    return {"saved": True}


@router.delete("/api-key-2")
async def remove_api_key_2(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove the user's second stored Anthropic API key."""
    await set_anthropic_api_key_2(current_user["id"], None)
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


class SpendCapBody(BaseModel):
    spend_cap: float = Field(ge=0.50, le=9999.99, description="Per-build spend cap in USD")


@router.put("/spend-cap")
async def save_spend_cap(
    body: SpendCapBody,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Set the per-build spend cap (USD). Must be >= 0.50."""
    await set_build_spend_cap(current_user["id"], body.spend_cap)
    return {"build_spend_cap": body.spend_cap}


@router.delete("/spend-cap")
async def remove_spend_cap(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Remove the per-build spend cap (unlimited)."""
    await set_build_spend_cap(current_user["id"], None)
    return {"removed": True}
