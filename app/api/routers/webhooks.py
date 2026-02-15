"""Webhook router -- receives GitHub push events."""

import logging

from fastapi import APIRouter, HTTPException, Request, status

from app.api.rate_limit import webhook_limiter
from app.config import settings
from app.services.audit_service import process_push_event
from app.webhooks import verify_github_signature

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    """Receive a GitHub push webhook event.

    Validates the X-Hub-Signature-256 header, then processes the push.
    Rate-limited to prevent abuse.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not webhook_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    signature = request.headers.get("X-Hub-Signature-256", "")
    body = await request.body()

    if not verify_github_signature(body, signature, settings.GITHUB_WEBHOOK_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    payload = await request.json()

    event_type = request.headers.get("X-GitHub-Event", "")
    if event_type != "push":
        return {"status": "ignored", "event": event_type}

    try:
        await process_push_event(payload)
    except Exception:
        logger.exception("Error processing push event")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error processing webhook",
        )
    return {"status": "accepted"}
