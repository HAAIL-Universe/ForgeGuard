"""Tests for auth router endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    """Set test configuration for all auth router tests."""
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")


client = TestClient(app)


def test_github_oauth_redirect_returns_url():
    response = client.get("/auth/github")
    assert response.status_code == 200
    data = response.json()
    assert "redirect_url" in data
    assert "github.com/login/oauth/authorize" in data["redirect_url"]
    assert "test-client-id" in data["redirect_url"]


def test_github_callback_rejects_invalid_state():
    response = client.get("/auth/github/callback?code=testcode&state=invalid")
    assert response.status_code == 400
    assert "Invalid OAuth state" in response.json()["detail"]


def test_auth_me_returns_401_without_token():
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_auth_me_returns_401_with_invalid_token():
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid.token.value"},
    )
    assert response.status_code == 401


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_auth_me_returns_user_with_valid_token(mock_get_user):
    mock_get_user.return_value = {
        "id": "11111111-1111-1111-1111-111111111111",
        "github_id": 12345,
        "github_login": "octocat",
        "avatar_url": "https://example.com/avatar.png",
    }

    token = create_token("11111111-1111-1111-1111-111111111111", "octocat")
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["github_login"] == "octocat"
    assert data["id"] == "11111111-1111-1111-1111-111111111111"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_auth_me_returns_401_when_user_not_found(mock_get_user):
    mock_get_user.return_value = None

    token = create_token("11111111-1111-1111-1111-111111111111", "ghost")
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401
