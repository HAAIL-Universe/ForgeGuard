"""WebSocket connection manager for real-time audit updates."""

import asyncio
import json
from uuid import UUID


class ConnectionManager:
    """Manages active WebSocket connections keyed by user_id."""

    def __init__(self) -> None:
        self._connections: dict[str, list] = {}  # user_id -> list of websockets
        self._lock = asyncio.Lock()

    async def connect(self, user_id: str, websocket) -> None:  # noqa: ANN001
        """Register a WebSocket connection for a user."""
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = []
            self._connections[user_id].append(websocket)

    async def disconnect(self, user_id: str, websocket) -> None:  # noqa: ANN001
        """Remove a WebSocket connection for a user."""
        async with self._lock:
            conns = self._connections.get(user_id, [])
            if websocket in conns:
                conns.remove(websocket)
            if not conns:
                self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: str, data: dict) -> None:
        """Send a JSON message to all connections for a specific user."""
        async with self._lock:
            conns = list(self._connections.get(user_id, []))
        message = json.dumps(data, default=str)
        dead = []
        for ws in conns:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                user_conns = self._connections.get(user_id, [])
                for ws in dead:
                    if ws in user_conns:
                        user_conns.remove(ws)
                if not user_conns:
                    self._connections.pop(user_id, None)

    async def broadcast_audit_update(self, user_id: str, audit_summary: dict) -> None:
        """Broadcast an audit_update event to the given user."""
        payload = {
            "type": "audit_update",
            "payload": audit_summary,
        }
        await self.send_to_user(user_id, payload)


manager = ConnectionManager()
