"""Tests for WebSocket connection manager."""

import asyncio

import pytest

from app.ws_manager import ConnectionManager, MAX_CONNECTIONS_PER_USER


class FakeWebSocket:
    """Fake WebSocket for testing send_text."""

    def __init__(self):
        self.messages: list[str] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise RuntimeError("WebSocket closed")
        self.messages.append(text)

    async def send_json(self, data: dict) -> None:
        import json
        await self.send_text(json.dumps(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


@pytest.mark.asyncio
async def test_connect_and_send():
    """Messages reach connected sockets."""
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    await mgr.connect("user-1", ws)
    await mgr.send_to_user("user-1", {"hello": "world"})
    assert len(ws.messages) == 1
    assert '"hello"' in ws.messages[0]


@pytest.mark.asyncio
async def test_disconnect_removes_socket():
    """After disconnect, no messages reach the socket."""
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    await mgr.connect("user-1", ws)
    await mgr.disconnect("user-1", ws)
    await mgr.send_to_user("user-1", {"hello": "world"})
    assert len(ws.messages) == 0


@pytest.mark.asyncio
async def test_multiple_connections():
    """Multiple sockets for one user all receive messages."""
    mgr = ConnectionManager()
    ws1 = FakeWebSocket()
    ws2 = FakeWebSocket()
    await mgr.connect("user-1", ws1)
    await mgr.connect("user-1", ws2)
    await mgr.send_to_user("user-1", {"data": 1})
    assert len(ws1.messages) == 1
    assert len(ws2.messages) == 1


@pytest.mark.asyncio
async def test_send_to_nonexistent_user():
    """Sending to a user with no connections does not error."""
    mgr = ConnectionManager()
    await mgr.send_to_user("nobody", {"data": 1})


@pytest.mark.asyncio
async def test_broadcast_audit_update():
    """broadcast_audit_update sends correctly typed message."""
    mgr = ConnectionManager()
    ws = FakeWebSocket()
    await mgr.connect("user-1", ws)
    await mgr.broadcast_audit_update("user-1", {"id": "abc", "status": "completed"})
    assert len(ws.messages) == 1
    import json
    msg = json.loads(ws.messages[0])
    assert msg["type"] == "audit_update"
    assert msg["payload"]["id"] == "abc"


@pytest.mark.asyncio
async def test_dead_socket_ignored():
    """Dead socket doesn't prevent messages to other sockets."""
    mgr = ConnectionManager()
    ws_dead = FakeWebSocket()
    ws_dead.closed = True
    ws_live = FakeWebSocket()
    await mgr.connect("user-1", ws_dead)
    await mgr.connect("user-1", ws_live)
    await mgr.send_to_user("user-1", {"x": 1})
    assert len(ws_live.messages) == 1
    assert len(ws_dead.messages) == 0


# ---------- R9: heartbeat ----------


@pytest.mark.asyncio
async def test_heartbeat_start_stop():
    """Heartbeat task can be started and stopped cleanly."""
    mgr = ConnectionManager()
    await mgr.start_heartbeat()
    assert mgr._heartbeat_task is not None
    await mgr.stop_heartbeat()
    assert mgr._heartbeat_task is None


@pytest.mark.asyncio
async def test_ping_all_removes_dead():
    """_ping_all removes connections that fail to respond."""
    mgr = ConnectionManager()
    ws_dead = FakeWebSocket()
    ws_dead.closed = True
    ws_live = FakeWebSocket()
    await mgr.connect("user-1", ws_dead)
    await mgr.connect("user-1", ws_live)
    await mgr._ping_all()
    # Dead socket pruned, live socket got ping
    assert len(ws_live.messages) == 1
    import json
    assert json.loads(ws_live.messages[0])["type"] == "ping"
    # Only live socket remains
    assert len(mgr._connections.get("user-1", [])) == 1


# ---------- R9.3: per-user connection limits ----------


@pytest.mark.asyncio
async def test_connection_limit_evicts_oldest():
    """When MAX_CONNECTIONS_PER_USER is reached, oldest is evicted."""
    mgr = ConnectionManager()
    sockets = [FakeWebSocket() for _ in range(MAX_CONNECTIONS_PER_USER + 1)]
    for ws in sockets:
        await mgr.connect("user-1", ws)

    # Oldest should have been closed
    assert sockets[0].closed
    assert sockets[0].close_code == 1008

    # Current connections should be capped
    assert len(mgr._connections["user-1"]) == MAX_CONNECTIONS_PER_USER

    # Newest socket should still be connected
    assert not sockets[-1].closed
