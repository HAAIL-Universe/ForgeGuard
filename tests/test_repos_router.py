"""Tests for repos router endpoints."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    """Set test configuration for all repos router tests."""
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")


USER_ID = "22222222-2222-2222-2222-222222222222"
MOCK_USER = {
    "id": UUID(USER_ID),
    "github_id": 99999,
    "github_login": "octocat",
    "avatar_url": "https://example.com/avatar.png",
    "access_token": "gho_testtoken123",
}

client = TestClient(app)


def _auth_header():
    token = create_token(USER_ID, "octocat")
    return {"Authorization": f"Bearer {token}"}


# ---------- GET /repos ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
def test_list_repos_returns_items(mock_get_repos, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_get_repos.return_value = [
        {
            "id": UUID("33333333-3333-3333-3333-333333333333"),
            "user_id": UUID(USER_ID),
            "github_repo_id": 12345,
            "full_name": "octocat/hello-world",
            "default_branch": "main",
            "webhook_id": 1,
            "webhook_active": True,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
            "total_count": 5,
            "pass_count": 5,
            "last_audit_at": None,
        }
    ]

    response = client.get("/repos", headers=_auth_header())
    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["full_name"] == "octocat/hello-world"
    assert data["items"][0]["health_score"] == "green"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repos_with_health", new_callable=AsyncMock)
def test_list_repos_empty(mock_get_repos, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_get_repos.return_value = []

    response = client.get("/repos", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_list_repos_requires_auth():
    response = client.get("/repos")
    assert response.status_code == 401


# ---------- GET /repos/available ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.list_user_repos", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repos_by_user", new_callable=AsyncMock)
def test_list_available_repos(mock_connected, mock_github, mock_svc_user, mock_dep_user):
    mock_dep_user.return_value = MOCK_USER
    mock_svc_user.return_value = MOCK_USER
    mock_github.return_value = [
        {"github_repo_id": 111, "full_name": "octocat/repo-a", "default_branch": "main", "private": False},
        {"github_repo_id": 222, "full_name": "octocat/repo-b", "default_branch": "main", "private": True},
    ]
    mock_connected.return_value = [
        {"github_repo_id": 111},
    ]

    response = client.get("/repos/available", headers=_auth_header())
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["full_name"] == "octocat/repo-b"


# ---------- POST /repos/connect ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_by_github_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.create_webhook", new_callable=AsyncMock)
@patch("app.services.repo_service.create_repo", new_callable=AsyncMock)
def test_connect_repo_success(mock_create, mock_webhook, mock_svc_user, mock_existing, mock_dep_user):
    mock_dep_user.return_value = MOCK_USER
    mock_existing.return_value = None
    mock_svc_user.return_value = MOCK_USER
    mock_webhook.return_value = 42
    mock_create.return_value = {
        "id": UUID("44444444-4444-4444-4444-444444444444"),
        "full_name": "octocat/new-repo",
        "webhook_active": True,
    }

    response = client.post(
        "/repos/connect",
        json={"github_repo_id": 555, "full_name": "octocat/new-repo", "default_branch": "main"},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["full_name"] == "octocat/new-repo"
    assert data["webhook_active"] is True


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_by_github_id", new_callable=AsyncMock)
def test_connect_repo_already_connected(mock_existing, mock_dep_user):
    mock_dep_user.return_value = MOCK_USER
    mock_existing.return_value = {"id": UUID("44444444-4444-4444-4444-444444444444")}

    response = client.post(
        "/repos/connect",
        json={"github_repo_id": 555, "full_name": "octocat/dup", "default_branch": "main"},
        headers=_auth_header(),
    )
    assert response.status_code == 409


def test_connect_repo_requires_auth():
    response = client.post(
        "/repos/connect",
        json={"github_repo_id": 1, "full_name": "x/y", "default_branch": "main"},
    )
    assert response.status_code == 401


# ---------- DELETE /repos/{id}/disconnect ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.delete_webhook", new_callable=AsyncMock)
@patch("app.services.repo_service.delete_repo", new_callable=AsyncMock)
def test_disconnect_repo_success(mock_del, mock_wh_del, mock_svc_user, mock_get_repo, mock_dep_user):
    mock_dep_user.return_value = MOCK_USER
    mock_get_repo.return_value = {
        "id": UUID("44444444-4444-4444-4444-444444444444"),
        "user_id": UUID(USER_ID),
        "full_name": "octocat/hello-world",
        "webhook_id": 42,
    }
    mock_svc_user.return_value = MOCK_USER
    mock_wh_del.return_value = None
    mock_del.return_value = True

    response = client.delete(
        "/repos/44444444-4444-4444-4444-444444444444/disconnect",
        headers=_auth_header(),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.repo_service.get_repo_by_id", new_callable=AsyncMock)
def test_disconnect_repo_not_found(mock_get_repo, mock_dep_user):
    mock_dep_user.return_value = MOCK_USER
    mock_get_repo.return_value = None

    response = client.delete(
        "/repos/44444444-4444-4444-4444-444444444444/disconnect",
        headers=_auth_header(),
    )
    assert response.status_code == 404


def test_disconnect_repo_requires_auth():
    response = client.delete("/repos/44444444-4444-4444-4444-444444444444/disconnect")
    assert response.status_code == 401
