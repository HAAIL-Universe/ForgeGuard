"""WebSocket router -- real-time audit result updates."""

import jwt as pyjwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import decode_token
from app.ws_manager import MAX_MESSAGE_SIZE, manager

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

    try:
        while True:
            # Keep connection alive; ignore client messages
            data = await websocket.receive_text()
            if len(data) > MAX_MESSAGE_SIZE:
                await websocket.close(code=1009, reason="Message too large")
                return
    except WebSocketDisconnect:
        await manager.disconnect(user_id, websocket)
