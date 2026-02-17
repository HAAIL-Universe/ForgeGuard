"""Tests for Phase 35: Build Spend Cap / Circuit Breaker / Cost Gate.

Covers:
- In-memory cost accumulation
- Cost gate enforcement (warning + hard cap)
- Cost ticker broadcast
- Circuit breaker endpoint
- Spend cap REST endpoints
- Live cost endpoint
- Cleanup on build finish
"""

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import build_service
from app.services.build_service import (
    CostCapExceeded,
    _accumulate_cost,
    _broadcast_cost_ticker,
    _build_api_calls,
    _build_cost_user,
    _build_cost_warned,
    _build_running_cost,
    _build_spend_caps,
    _build_total_input_tokens,
    _build_total_output_tokens,
    _check_cost_gate,
    _cleanup_cost_tracking,
    _init_cost_tracking,
    _last_cost_ticker,
    get_build_cost_live,
)


_USER_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


@pytest.fixture(autouse=True)
def _clean_cost_state():
    """Ensure cost tracking state is clean before/after each test."""
    bid = str(_BUILD_ID)
    yield
    _build_running_cost.pop(bid, None)
    _build_api_calls.pop(bid, None)
    _build_total_input_tokens.pop(bid, None)
    _build_total_output_tokens.pop(bid, None)
    _build_spend_caps.pop(bid, None)
    _build_cost_warned.pop(bid, None)
    _last_cost_ticker.pop(bid, None)
    _build_cost_user.pop(bid, None)


# ---------------------------------------------------------------------------
# _init / _cleanup
# ---------------------------------------------------------------------------


def test_init_cost_tracking():
    _init_cost_tracking(_BUILD_ID, _USER_ID, 25.0)
    bid = str(_BUILD_ID)
    assert _build_running_cost[bid] == Decimal(0)
    assert _build_api_calls[bid] == 0
    assert _build_spend_caps[bid] == 25.0
    assert _build_cost_user[bid] == _USER_ID


def test_cleanup_cost_tracking():
    _init_cost_tracking(_BUILD_ID, _USER_ID, 10.0)
    _cleanup_cost_tracking(_BUILD_ID)
    bid = str(_BUILD_ID)
    assert bid not in _build_running_cost
    assert bid not in _build_api_calls
    assert bid not in _build_spend_caps
    assert bid not in _build_cost_user


# ---------------------------------------------------------------------------
# _accumulate_cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accumulate_cost_tracks_running_total(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_COST_TICKER_INTERVAL", 999)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)  # unlimited
    bid = str(_BUILD_ID)

    with patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock):
        await _accumulate_cost(_BUILD_ID, 1000, 500, "claude-opus-4", Decimal("0.05"))
        assert _build_running_cost[bid] == Decimal("0.05")
        assert _build_api_calls[bid] == 1
        assert _build_total_input_tokens[bid] == 1000
        assert _build_total_output_tokens[bid] == 500

        await _accumulate_cost(_BUILD_ID, 2000, 1000, "claude-opus-4", Decimal("0.10"))
        assert _build_running_cost[bid] == Decimal("0.15")
        assert _build_api_calls[bid] == 2
        assert _build_total_input_tokens[bid] == 3000
        assert _build_total_output_tokens[bid] == 1500


# ---------------------------------------------------------------------------
# _check_cost_gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_gate_no_cap_unlimited(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 0.0)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("999.99")
    # Should not raise
    await _check_cost_gate(_BUILD_ID)


@pytest.mark.asyncio
async def test_cost_gate_user_cap_exceeded(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 100.0)
    _init_cost_tracking(_BUILD_ID, _USER_ID, 5.0)  # user cap = $5
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("5.01")

    with patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state._fail_build", new_callable=AsyncMock):
        with pytest.raises(CostCapExceeded):
            await _check_cost_gate(_BUILD_ID)
    assert bid in build_service._cancel_flags
    build_service._cancel_flags.discard(bid)


@pytest.mark.asyncio
async def test_cost_gate_server_cap_used_when_no_user_cap(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 10.0)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)  # no user cap
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("10.50")

    with patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
         patch("app.services.build._state._fail_build", new_callable=AsyncMock):
        with pytest.raises(CostCapExceeded):
            await _check_cost_gate(_BUILD_ID)
    build_service._cancel_flags.discard(bid)


@pytest.mark.asyncio
async def test_cost_gate_warning_at_threshold(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 10.0)
    monkeypatch.setattr("app.services.build._state.settings.BUILD_COST_WARN_PCT", 80)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("8.50")  # 85% — above warning, below cap

    mock_broadcast = AsyncMock()
    with patch("app.services.build._state._broadcast_build_event", mock_broadcast):
        await _check_cost_gate(_BUILD_ID)

    assert _build_cost_warned[bid] is True
    # Should have sent cost_warning event
    mock_broadcast.assert_called_once()
    call_args = mock_broadcast.call_args
    assert call_args[0][2] == "cost_warning"


@pytest.mark.asyncio
async def test_cost_gate_warning_sent_only_once(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 10.0)
    monkeypatch.setattr("app.services.build._state.settings.BUILD_COST_WARN_PCT", 80)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("8.50")

    mock_broadcast = AsyncMock()
    with patch("app.services.build._state._broadcast_build_event", mock_broadcast):
        await _check_cost_gate(_BUILD_ID)
        await _check_cost_gate(_BUILD_ID)

    # Only one warning event
    assert mock_broadcast.call_count == 1


@pytest.mark.asyncio
async def test_cost_gate_below_threshold_no_warning(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 10.0)
    monkeypatch.setattr("app.services.build._state.settings.BUILD_COST_WARN_PCT", 80)
    _init_cost_tracking(_BUILD_ID, _USER_ID, None)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("5.00")  # 50% — below warning

    mock_broadcast = AsyncMock()
    with patch("app.services.build._state._broadcast_build_event", mock_broadcast):
        await _check_cost_gate(_BUILD_ID)

    mock_broadcast.assert_not_called()
    assert _build_cost_warned[bid] is False


# ---------------------------------------------------------------------------
# get_build_cost_live
# ---------------------------------------------------------------------------


def test_get_build_cost_live():
    _init_cost_tracking(_BUILD_ID, _USER_ID, 25.0)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("3.14")
    _build_api_calls[bid] = 7
    _build_total_input_tokens[bid] = 5000
    _build_total_output_tokens[bid] = 2000

    result = get_build_cost_live(bid)
    assert result["total_cost_usd"] == pytest.approx(3.14)
    assert result["api_calls"] == 7
    assert result["tokens_in"] == 5000
    assert result["tokens_out"] == 2000
    assert result["spend_cap"] == 25.0


def test_get_build_cost_live_empty():
    result = get_build_cost_live("nonexistent")
    assert result["total_cost_usd"] == 0.0
    assert result["api_calls"] == 0


# ---------------------------------------------------------------------------
# _broadcast_cost_ticker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_cost_ticker(monkeypatch):
    monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 50.0)
    _init_cost_tracking(_BUILD_ID, _USER_ID, 20.0)
    bid = str(_BUILD_ID)
    _build_running_cost[bid] = Decimal("4.0")
    _build_api_calls[bid] = 3
    _build_total_input_tokens[bid] = 1000
    _build_total_output_tokens[bid] = 500

    mock_broadcast = AsyncMock()
    with patch("app.services.build._state._broadcast_build_event", mock_broadcast):
        await _broadcast_cost_ticker(_BUILD_ID, _USER_ID)

    mock_broadcast.assert_called_once()
    payload = mock_broadcast.call_args[0][3]
    assert payload["total_cost_usd"] == pytest.approx(4.0)
    assert payload["api_calls"] == 3
    assert payload["spend_cap"] == 20.0
    assert payload["pct_used"] == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Auth router: spend cap endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def _auth_config(monkeypatch):
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")


_AUTH_USER = {
    "id": "11111111-1111-1111-1111-111111111111",
    "github_id": 12345,
    "github_login": "octocat",
    "avatar_url": None,
    "anthropic_api_key": None,
    "anthropic_api_key_2": None,
    "build_spend_cap": None,
}


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.routers.auth.set_build_spend_cap", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_save_spend_cap(mock_get_user, mock_set_cap):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    mock_get_user.return_value = _AUTH_USER
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.put(
        "/auth/spend-cap",
        json={"spend_cap": 25.0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["build_spend_cap"] == 25.0
    mock_set_cap.assert_called_once()


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_save_spend_cap_rejects_zero(mock_get_user):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    mock_get_user.return_value = _AUTH_USER
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.put(
        "/auth/spend-cap",
        json={"spend_cap": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_save_spend_cap_rejects_negative(mock_get_user):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    mock_get_user.return_value = _AUTH_USER
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.put(
        "/auth/spend-cap",
        json={"spend_cap": -5},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.routers.auth.set_build_spend_cap", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_remove_spend_cap(mock_get_user, mock_set_cap):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    mock_get_user.return_value = _AUTH_USER
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.delete(
        "/auth/spend-cap",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["removed"] is True
    mock_set_cap.assert_called_once()


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_me_includes_spend_cap(mock_get_user):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    user_with_cap = {**_AUTH_USER, "build_spend_cap": Decimal("25.00")}
    mock_get_user.return_value = user_with_cap
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["build_spend_cap"] == 25.0


@pytest.mark.usefixtures("_auth_config")
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_me_spend_cap_null_when_not_set(mock_get_user):
    from app.auth import create_token
    from fastapi.testclient import TestClient
    from app.main import app

    mock_get_user.return_value = _AUTH_USER
    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    client = TestClient(app)
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["build_spend_cap"] is None


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_config_cost_defaults():
    from app.config import settings

    assert hasattr(settings, "BUILD_MAX_COST_USD")
    assert hasattr(settings, "BUILD_COST_WARN_PCT")
    assert hasattr(settings, "BUILD_COST_TICKER_INTERVAL")
    assert settings.BUILD_MAX_COST_USD == 50.0
    assert settings.BUILD_COST_WARN_PCT == 80
    assert settings.BUILD_COST_TICKER_INTERVAL == 5
