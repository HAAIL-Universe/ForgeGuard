"""Tests for WebSocket router endpoint."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _make_token_payload(user_id: str = "test-user-id"):
    return {"sub": user_id, "github_login": "testuser"}


def test_ws_rejects_missing_token(client):
    """WebSocket connection without token should be rejected."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws"):
            pass


def test_ws_rejects_invalid_token(client):
    """WebSocket connection with invalid token should be rejected."""
    with pytest.raises(Exception):
        with client.websocket_connect("/ws?token=badtoken"):
            pass


@patch("app.api.routers.ws.decode_token")
def test_ws_accepts_valid_token(mock_decode, client):
    """WebSocket connection with valid token should be accepted."""
    mock_decode.return_value = _make_token_payload()
    with client.websocket_connect("/ws?token=validtoken") as ws:
        # Connection established - no immediate message expected
        # Just verify it connected
        assert ws is not None


@patch("app.api.routers.ws.decode_token")
@patch("app.api.routers.ws.manager")
def test_ws_connects_and_disconnects(mock_manager, mock_decode, client):
    """WebSocket lifecycle: connect -> manager.connect called."""
    mock_decode.return_value = _make_token_payload("uid-123")
    mock_manager.connect = AsyncMock()
    mock_manager.disconnect = AsyncMock()

    with client.websocket_connect("/ws?token=validtoken"):
        mock_manager.connect.assert_called_once()
        call_args = mock_manager.connect.call_args
        assert call_args[0][0] == "uid-123"
