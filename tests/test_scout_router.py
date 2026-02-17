"""Tests for scout router endpoints."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")


USER_ID = "22222222-2222-2222-2222-222222222222"
REPO_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "44444444-4444-4444-4444-444444444444"

MOCK_USER = {
    "id": UUID(USER_ID),
    "github_id": 99999,
    "github_login": "octocat",
    "avatar_url": "https://example.com/avatar.png",
    "access_token": "gho_testtoken123",
}

client = TestClient(app)


def _auth_header():
    token = create_token(USER_ID, "scout-user")
    return {"Authorization": f"Bearer {token}"}


# ---------- POST /scout/{repo_id}/run ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.start_scout_run", new_callable=AsyncMock)
def test_trigger_scout(mock_start, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_start.return_value = {
        "id": RUN_ID,
        "status": "running",
        "repo_name": "owner/repo",
    }

    resp = client.post(
        f"/scout/{REPO_ID}/run",
        headers=_auth_header(),
        json={"hypothesis": "I think auth is leaking"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "running"
    assert data["id"] == RUN_ID
    mock_start.assert_called_once()


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.start_scout_run", new_callable=AsyncMock)
def test_trigger_scout_no_body(mock_start, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_start.return_value = {
        "id": RUN_ID,
        "status": "running",
        "repo_name": "owner/repo",
    }

    resp = client.post(f"/scout/{REPO_ID}/run", headers=_auth_header())
    assert resp.status_code == 200


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.start_scout_run", new_callable=AsyncMock)
def test_trigger_scout_not_found(mock_start, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_start.side_effect = ValueError("Repo not found")

    resp = client.post(f"/scout/{REPO_ID}/run", headers=_auth_header())
    assert resp.status_code == 404


def test_trigger_scout_unauthenticated():
    resp = client.post(f"/scout/{REPO_ID}/run")
    assert resp.status_code in (401, 403)


# ---------- GET /scout/history ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.get_scout_history", new_callable=AsyncMock)
def test_scout_history(mock_history, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_history.return_value = [
        {
            "id": RUN_ID,
            "repo_id": REPO_ID,
            "repo_name": "owner/repo",
            "status": "completed",
            "hypothesis": None,
            "checks_passed": 10,
            "checks_failed": 1,
            "checks_warned": 1,
            "started_at": "2026-02-16T10:00:00+00:00",
            "completed_at": "2026-02-16T10:00:05+00:00",
        }
    ]

    resp = client.get("/scout/history", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["runs"]) == 1
    assert data["runs"][0]["checks_passed"] == 10


# ---------- GET /scout/{repo_id}/history ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.get_scout_history", new_callable=AsyncMock)
def test_scout_repo_history(mock_history, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_history.return_value = []

    resp = client.get(f"/scout/{REPO_ID}/history", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


# ---------- GET /scout/runs/{run_id} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.get_scout_detail", new_callable=AsyncMock)
def test_scout_run_detail(mock_detail, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_detail.return_value = {
        "id": RUN_ID,
        "repo_id": REPO_ID,
        "repo_name": "owner/repo",
        "status": "completed",
        "checks": [{"code": "A1", "name": "Scope compliance", "result": "PASS", "detail": "ok"}],
        "warnings": [{"code": "W1", "name": "Secrets in diff", "result": "PASS", "detail": "ok"}],
        "checks_passed": 11,
        "checks_failed": 0,
        "checks_warned": 1,
    }

    resp = client.get(f"/scout/runs/{RUN_ID}", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert data["checks_passed"] == 11
    assert len(data["checks"]) == 1


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.scout.get_scout_detail", new_callable=AsyncMock)
def test_scout_run_detail_not_found(mock_detail, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_detail.side_effect = ValueError("Scout run not found")

    resp = client.get(f"/scout/runs/{RUN_ID}", headers=_auth_header())
    assert resp.status_code == 404
