"""Tests for app/api/routers/builds.py -- build endpoint tests."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_USER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_USER = {
    "id": uuid.UUID(_USER_ID),
    "github_id": 12345,
    "github_login": "testuser",
    "avatar_url": "https://example.com/avatar.png",
    "access_token": "gho_test",
}
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _build(**overrides):
    defaults = {
        "id": _BUILD_ID,
        "project_id": _PROJECT_ID,
        "phase": "Phase 0",
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "loop_count": 0,
        "error_detail": None,
        "created_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _auth_header():
    token = create_token(_USER_ID, "testuser")
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build(mock_get_user, mock_start, client):
    """POST /projects/{id}/build starts a build."""
    mock_get_user.return_value = _USER
    mock_start.return_value = _build()

    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    mock_start.assert_called_once_with(_PROJECT_ID, _USER["id"])


@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_not_found(mock_get_user, mock_start, client):
    """POST /projects/{id}/build returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_start.side_effect = ValueError("Project not found")

    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())

    assert resp.status_code == 404


@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_no_contracts(mock_get_user, mock_start, client):
    """POST /projects/{id}/build returns 400 when contracts missing."""
    mock_get_user.return_value = _USER
    mock_start.side_effect = ValueError("No contracts found. Generate contracts before building.")

    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())

    assert resp.status_code == 400


@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_already_running(mock_get_user, mock_start, client):
    """POST /projects/{id}/build returns 400 when build already running."""
    mock_get_user.return_value = _USER
    mock_start.side_effect = ValueError("A build is already in progress for this project")

    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build/cancel
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.cancel_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_cancel_build(mock_get_user, mock_cancel, client):
    """POST /projects/{id}/build/cancel cancels an active build."""
    mock_get_user.return_value = _USER
    mock_cancel.return_value = _build(status="cancelled")

    resp = client.post(f"/projects/{_PROJECT_ID}/build/cancel", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@patch("app.api.routers.builds.build_service.cancel_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_cancel_build_no_active(mock_get_user, mock_cancel, client):
    """POST /projects/{id}/build/cancel returns 400 if no active build."""
    mock_get_user.return_value = _USER
    mock_cancel.side_effect = ValueError("No active build to cancel")

    resp = client.post(f"/projects/{_PROJECT_ID}/build/cancel", headers=_auth_header())

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/status
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_status", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_status(mock_get_user, mock_status, client):
    """GET /projects/{id}/build/status returns current build."""
    mock_get_user.return_value = _USER
    mock_status.return_value = _build(status="running", phase="Phase 2")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/status", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert resp.json()["phase"] == "Phase 2"


@patch("app.api.routers.builds.build_service.get_build_status", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_status_no_builds(mock_get_user, mock_status, client):
    """GET /projects/{id}/build/status returns 400 when no builds."""
    mock_get_user.return_value = _USER
    mock_status.side_effect = ValueError("No builds found for this project")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/status", headers=_auth_header())

    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/logs
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_logs", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_logs(mock_get_user, mock_logs, client):
    """GET /projects/{id}/build/logs returns paginated logs."""
    mock_get_user.return_value = _USER
    mock_logs.return_value = ([{"message": "log1"}, {"message": "log2"}], 42)

    resp = client.get(
        f"/projects/{_PROJECT_ID}/build/logs",
        params={"limit": 10, "offset": 0},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 42
    assert len(data["items"]) == 2


@patch("app.api.routers.builds.build_service.get_build_logs", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_logs_not_found(mock_get_user, mock_logs, client):
    """GET /projects/{id}/build/logs returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_logs.side_effect = ValueError("Project not found")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/logs", headers=_auth_header())

    assert resp.status_code == 404


def test_build_endpoints_require_auth(client):
    """All build endpoints return 401 without auth."""
    pid = uuid.uuid4()
    endpoints = [
        ("POST", f"/projects/{pid}/build"),
        ("POST", f"/projects/{pid}/build/cancel"),
        ("GET", f"/projects/{pid}/build/status"),
        ("GET", f"/projects/{pid}/build/logs"),
        ("GET", f"/projects/{pid}/build/summary"),
        ("GET", f"/projects/{pid}/build/instructions"),
    ]
    for method, url in endpoints:
        resp = client.request(method, url)
        assert resp.status_code == 401, f"{method} {url} should require auth"


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/summary
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_summary", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_summary(mock_get_user, mock_summary, client):
    """GET /projects/{id}/build/summary returns cost breakdown."""
    mock_get_user.return_value = _USER
    mock_summary.return_value = {
        "build": _build(status="completed"),
        "cost": {
            "total_input_tokens": 5000,
            "total_output_tokens": 2500,
            "total_cost_usd": 0.26,
            "phases": [],
        },
        "elapsed_seconds": 120.5,
        "loop_count": 0,
    }

    resp = client.get(f"/projects/{_PROJECT_ID}/build/summary", headers=_auth_header())

    assert resp.status_code == 200
    data = resp.json()
    assert data["cost"]["total_input_tokens"] == 5000
    assert data["elapsed_seconds"] == 120.5


@patch("app.api.routers.builds.build_service.get_build_summary", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_summary_not_found(mock_get_user, mock_summary, client):
    """GET /projects/{id}/build/summary returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_summary.side_effect = ValueError("Project not found")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/summary", headers=_auth_header())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/instructions
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_instructions", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_instructions(mock_get_user, mock_instr, client):
    """GET /projects/{id}/build/instructions returns deploy instructions."""
    mock_get_user.return_value = _USER
    mock_instr.return_value = {
        "project_name": "TestApp",
        "instructions": "# Deploy\n1. Clone repo\n2. Run boot.ps1",
    }

    resp = client.get(f"/projects/{_PROJECT_ID}/build/instructions", headers=_auth_header())

    assert resp.status_code == 200
    data = resp.json()
    assert data["project_name"] == "TestApp"
    assert "Clone repo" in data["instructions"]


@patch("app.api.routers.builds.build_service.get_build_instructions", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_instructions_not_found(mock_get_user, mock_instr, client):
    """GET /projects/{id}/build/instructions returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_instr.side_effect = ValueError("Project not found")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/instructions", headers=_auth_header())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: Rate limiting on build start
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_limiter")
@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_rate_limited(mock_get_user, mock_start, mock_limiter, client):
    """POST /projects/{id}/build returns 429 when rate limited."""
    mock_get_user.return_value = _USER
    mock_limiter.is_allowed.return_value = False

    resp = client.post(f"/projects/{_PROJECT_ID}/build", headers=_auth_header())

    assert resp.status_code == 429
    mock_start.assert_not_called()
