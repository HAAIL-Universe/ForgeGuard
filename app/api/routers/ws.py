"""WebSocket router -- real-time audit result updates."""

import logging

import jwt as pyjwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import decode_token
from app.ws_manager import MAX_MESSAGE_SIZE, manager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time audit updates.

    Auth via query param: /ws?token=<jwt>
    Server sends messages of type "audit_update" with AuditRunSummary payload.
    """
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    try:
        payload = decode_token(token)
    except (pyjwt.ExpiredSignatureError, pyjwt.PyJWTError):
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = payload.get("sub")
    if not user_id:
        await websocket.close(code=4001, reason="Invalid token payload")
        return

    await websocket.accept()
    await manager.connect(user_id, websocket)
    count = manager.connection_count(user_id)
    logger.info("WS open  user=%s conns=%d", user_id[:8], count)

    try:
        while True:
            # Keep connection alive; ignore client messages
            data = await websocket.receive_text()
            if len(data) > MAX_MESSAGE_SIZE:
                await websocket.close(code=1009, reason="Message too large")
                return
    except WebSocketDisconnect:
        logger.info("WS close user=%s (client disconnect)", user_id[:8])
    except Exception:
        logger.exception("WS error user=%s", user_id[:8])
    finally:
        await manager.disconnect(user_id, websocket)
        count = manager.connection_count(user_id)
        logger.info("WS cleaned up user=%s remaining=%d", user_id[:8], count)
