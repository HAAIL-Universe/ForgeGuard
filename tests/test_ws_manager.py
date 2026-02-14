"""Tests for WebSocket connection manager."""

import asyncio

import pytest

from app.ws_manager import ConnectionManager


class FakeWebSocket:
    """Fake WebSocket for testing send_text."""

    def __init__(self):
        self.messages: list[str] = []
        self.closed = False

    async def send_text(self, text: str) -> None:
        if self.closed:
            raise RuntimeError("WebSocket closed")
        self.messages.append(text)


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
