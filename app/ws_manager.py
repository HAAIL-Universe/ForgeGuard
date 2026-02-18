"""WebSocket connection manager for real-time audit updates."""

import asyncio
import json
import logging
from uuid import UUID

logger = logging.getLogger(__name__)

# Heartbeat interval (seconds) — ping all connections periodically
HEARTBEAT_INTERVAL = 30

# Maximum connections per user before oldest is evicted
MAX_CONNECTIONS_PER_USER = 3

# Maximum inbound message size (bytes)
MAX_MESSAGE_SIZE = 4096


class ConnectionManager:
    """Manages active WebSocket connections keyed by user_id."""

    def __init__(self) -> None:
        self._connections: dict[str, list] = {}  # user_id -> list of websockets
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task | None = None

    # ── lifecycle ─────────────────────────────────────────────

    async def start_heartbeat(self) -> None:
        """Start the background heartbeat loop (call from lifespan startup)."""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        """Cancel the heartbeat task (call from lifespan shutdown)."""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    # ── connection management ─────────────────────────────────

    async def connect(self, user_id: str, websocket) -> None:  # noqa: ANN001
        """Register a WebSocket connection for a user.

        Enforces MAX_CONNECTIONS_PER_USER — oldest connection is evicted
        when the limit is reached.
        """
        async with self._lock:
            if user_id not in self._connections:
                self._connections[user_id] = []
            conns = self._connections[user_id]

            # Evict oldest if at capacity
            while len(conns) >= MAX_CONNECTIONS_PER_USER:
                oldest = conns.pop(0)
                try:
                    await oldest.close(code=1008, reason="Connection limit reached")
                except Exception:
                    pass

            conns.append(websocket)

    def connection_count(self, user_id: str) -> int:
        """Return the number of active connections for a user (lock-free)."""
        return len(self._connections.get(user_id, []))

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
                await asyncio.wait_for(ws.send_text(message), timeout=5.0)
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

    # ── heartbeat ─────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        """Ping every connection periodically and prune dead ones."""
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            try:
                await self._ping_all()
            except Exception:
                logger.exception("Heartbeat sweep error")

    async def _ping_all(self) -> None:
        """Send a ping frame to every connection; remove dead ones."""
        async with self._lock:
            snapshot = {uid: list(conns) for uid, conns in self._connections.items()}

        for uid, conns in snapshot.items():
            dead: list = []
            for ws in conns:
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    dead.append(ws)
            if dead:
                async with self._lock:
                    user_conns = self._connections.get(uid, [])
                    for ws in dead:
                        if ws in user_conns:
                            user_conns.remove(ws)
                    if not user_conns:
                        self._connections.pop(uid, None)


manager = ConnectionManager()
