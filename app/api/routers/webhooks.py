"""Webhook router -- receives GitHub push events."""

from fastapi import APIRouter, HTTPException, Request, status

from app.config import settings
from app.services.audit_service import process_push_event
from app.webhooks import verify_github_signature

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/github")
async def github_webhook(request: Request) -> dict:
    """Receive a GitHub push webhook event.

    Validates the X-Hub-Signature-256 header, then processes the push.
    """
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

    await process_push_event(payload)
    return {"status": "accepted"}
