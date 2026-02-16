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
    mock_start.assert_called_once_with(
        _PROJECT_ID, _USER["id"],
        target_type=None, target_ref=None,
        branch="main",
    )


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
        ("POST", f"/projects/{pid}/build/resume"),
        ("POST", f"/projects/{pid}/build/interject"),
        ("GET", f"/projects/{pid}/build/status"),
        ("GET", f"/projects/{pid}/build/logs"),
        ("GET", f"/projects/{pid}/build/summary"),
        ("GET", f"/projects/{pid}/build/instructions"),
        ("GET", f"/projects/{pid}/builds"),
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


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build with target params
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_limiter")
@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_with_target(mock_get_user, mock_start, mock_limiter, client):
    """POST /projects/{id}/build with target_type and target_ref."""
    mock_get_user.return_value = _USER
    mock_start.return_value = _build()
    mock_limiter.is_allowed.return_value = True

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build",
        headers=_auth_header(),
        json={"target_type": "local_path", "target_ref": "/tmp/test"},
    )

    assert resp.status_code == 200
    mock_start.assert_called_once()
    call_kwargs = mock_start.call_args
    assert call_kwargs.kwargs.get("target_type") == "local_path"
    assert call_kwargs.kwargs.get("target_ref") == "/tmp/test"


@patch("app.api.routers.builds.build_limiter")
@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_with_github_target(mock_get_user, mock_start, mock_limiter, client):
    """POST /projects/{id}/build with github_new target."""
    mock_get_user.return_value = _USER
    mock_start.return_value = _build()
    mock_limiter.is_allowed.return_value = True

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build",
        headers=_auth_header(),
        json={"target_type": "github_new", "target_ref": "my-new-repo"},
    )

    assert resp.status_code == 200
    mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/files
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_files", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_files(mock_get_user, mock_files, client):
    """GET /projects/{id}/build/files returns file list."""
    mock_get_user.return_value = _USER
    mock_files.return_value = [
        {"path": "src/main.py", "size_bytes": 42, "language": "python", "created_at": "2025-01-01T00:00:00Z"},
        {"path": "package.json", "size_bytes": 200, "language": "json", "created_at": "2025-01-01T00:00:01Z"},
    ]

    resp = client.get(f"/projects/{_PROJECT_ID}/build/files", headers=_auth_header())

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["items"][0]["path"] == "src/main.py"


@patch("app.api.routers.builds.build_service.get_build_files", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_files_not_found(mock_get_user, mock_files, client):
    """GET /projects/{id}/build/files returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_files.side_effect = ValueError("Project not found")

    resp = client.get(f"/projects/{_PROJECT_ID}/build/files", headers=_auth_header())

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/build/files/{path}
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.get_build_file_content", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_file_content(mock_get_user, mock_content, client):
    """GET /projects/{id}/build/files/{path} returns file content."""
    mock_get_user.return_value = _USER
    mock_content.return_value = {
        "path": "src/main.py",
        "content": "print('hello')\n",
        "size_bytes": 16,
        "language": "python",
    }

    resp = client.get(
        f"/projects/{_PROJECT_ID}/build/files/src/main.py",
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["path"] == "src/main.py"
    assert "print('hello')" in data["content"]


@patch("app.api.routers.builds.build_service.get_build_file_content", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_get_build_file_content_not_found(mock_get_user, mock_content, client):
    """GET /projects/{id}/build/files/{path} returns 404 for missing file."""
    mock_get_user.return_value = _USER
    mock_content.side_effect = ValueError("File not found: nonexistent.py")

    resp = client.get(
        f"/projects/{_PROJECT_ID}/build/files/nonexistent.py",
        headers=_auth_header(),
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build/resume (Phase 14)
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.resume_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_resume_build(mock_get_user, mock_resume, client):
    """POST /projects/{id}/build/resume resumes a paused build."""
    mock_get_user.return_value = _USER
    mock_resume.return_value = _build(status="running")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/resume",
        headers=_auth_header(),
        json={"action": "retry"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    mock_resume.assert_called_once_with(
        _PROJECT_ID, _USER["id"], action="retry",
    )


@patch("app.api.routers.builds.build_service.resume_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_resume_build_not_paused(mock_get_user, mock_resume, client):
    """POST /projects/{id}/build/resume returns 400 if not paused."""
    mock_get_user.return_value = _USER
    mock_resume.side_effect = ValueError("No paused build to resume")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/resume",
        headers=_auth_header(),
        json={"action": "retry"},
    )

    assert resp.status_code == 400


@patch("app.api.routers.builds.build_service.resume_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_resume_build_skip(mock_get_user, mock_resume, client):
    """POST /projects/{id}/build/resume with skip action."""
    mock_get_user.return_value = _USER
    mock_resume.return_value = _build(status="running")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/resume",
        headers=_auth_header(),
        json={"action": "skip"},
    )

    assert resp.status_code == 200
    mock_resume.assert_called_once_with(
        _PROJECT_ID, _USER["id"], action="skip",
    )


@patch("app.api.routers.builds.build_service.resume_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_resume_build_abort(mock_get_user, mock_resume, client):
    """POST /projects/{id}/build/resume with abort action."""
    mock_get_user.return_value = _USER
    mock_resume.return_value = _build(status="failed")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/resume",
        headers=_auth_header(),
        json={"action": "abort"},
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build/interject (Phase 14)
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.interject_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_interject_build(mock_get_user, mock_interject, client):
    """POST /projects/{id}/build/interject sends a message."""
    mock_get_user.return_value = _USER
    mock_interject.return_value = {
        "status": "queued",
        "build_id": str(_BUILD_ID),
        "message": "fix the auth module",
    }

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/interject",
        headers=_auth_header(),
        json={"message": "fix the auth module"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


@patch("app.api.routers.builds.build_service.interject_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_interject_build_no_active(mock_get_user, mock_interject, client):
    """POST /projects/{id}/build/interject returns 400 if no active build."""
    mock_get_user.return_value = _USER
    mock_interject.side_effect = ValueError("No active build to interject")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/interject",
        headers=_auth_header(),
        json={"message": "hello"},
    )

    assert resp.status_code == 400


@patch("app.api.routers.builds.build_service.interject_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_interject_build_not_found(mock_get_user, mock_interject, client):
    """POST /projects/{id}/build/interject returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_interject.side_effect = ValueError("Project not found")

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build/interject",
        headers=_auth_header(),
        json={"message": "hello"},
    )

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests: GET /projects/{id}/builds (list builds)
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.list_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_list_builds(mock_get_user, mock_list, client):
    """GET /projects/{id}/builds returns build history."""
    mock_get_user.return_value = _USER
    mock_list.return_value = [
        {
            "id": str(_BUILD_ID),
            "phase": "Phase 2",
            "status": "completed",
            "branch": "main",
            "loop_count": 12,
            "started_at": None,
            "completed_at": None,
            "created_at": "2025-01-01T00:00:00Z",
            "error_detail": None,
        }
    ]

    resp = client.get(f"/projects/{_PROJECT_ID}/builds", headers=_auth_header())

    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert len(data["items"]) == 1
    assert data["items"][0]["branch"] == "main"


@patch("app.api.routers.builds.build_service.list_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_list_builds_not_found(mock_get_user, mock_list, client):
    """GET /projects/{id}/builds returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_list.side_effect = ValueError("Project not found")

    resp = client.get(f"/projects/{_PROJECT_ID}/builds", headers=_auth_header())

    assert resp.status_code == 404


@patch("app.api.routers.builds.build_service.list_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_list_builds_empty(mock_get_user, mock_list, client):
    """GET /projects/{id}/builds returns empty list."""
    mock_get_user.return_value = _USER
    mock_list.return_value = []

    resp = client.get(f"/projects/{_PROJECT_ID}/builds", headers=_auth_header())

    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Tests: POST /projects/{id}/build with branch
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_limiter")
@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_with_branch(mock_get_user, mock_start, mock_limiter, client):
    """POST /projects/{id}/build passes branch parameter."""
    mock_get_user.return_value = _USER
    mock_limiter.is_allowed.return_value = True
    mock_start.return_value = _build()

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build",
        headers=_auth_header(),
        json={"target_type": "github_existing", "target_ref": "user/repo", "branch": "forge/v2"},
    )

    assert resp.status_code == 200
    mock_start.assert_called_once()
    call_kwargs = mock_start.call_args
    assert call_kwargs.kwargs["branch"] == "forge/v2"


@patch("app.api.routers.builds.build_limiter")
@patch("app.api.routers.builds.build_service.start_build", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_start_build_default_branch(mock_get_user, mock_start, mock_limiter, client):
    """POST /projects/{id}/build defaults to main branch."""
    mock_get_user.return_value = _USER
    mock_limiter.is_allowed.return_value = True
    mock_start.return_value = _build()

    resp = client.post(
        f"/projects/{_PROJECT_ID}/build",
        headers=_auth_header(),
        json={"target_type": "github_existing", "target_ref": "user/repo"},
    )

    assert resp.status_code == 200
    call_kwargs = mock_start.call_args
    assert call_kwargs.kwargs["branch"] == "main"


# ---------------------------------------------------------------------------
# Tests: DELETE /projects/{id}/builds (delete builds)
# ---------------------------------------------------------------------------


@patch("app.api.routers.builds.build_service.delete_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_delete_builds_success(mock_get_user, mock_delete, client):
    """DELETE /projects/{id}/builds deletes selected builds."""
    mock_get_user.return_value = _USER
    mock_delete.return_value = 2

    resp = client.request(
        "DELETE",
        f"/projects/{_PROJECT_ID}/builds",
        headers=_auth_header(),
        json={"build_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted"] == 2


@patch("app.api.routers.builds.build_service.delete_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_delete_builds_not_found(mock_get_user, mock_delete, client):
    """DELETE /projects/{id}/builds returns 404 for missing project."""
    mock_get_user.return_value = _USER
    mock_delete.side_effect = ValueError("Project not found")

    resp = client.request(
        "DELETE",
        f"/projects/{_PROJECT_ID}/builds",
        headers=_auth_header(),
        json={"build_ids": [str(uuid.uuid4())]},
    )

    assert resp.status_code == 404


@patch("app.api.routers.builds.build_service.delete_builds", new_callable=AsyncMock)
@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_delete_builds_no_eligible(mock_get_user, mock_delete, client):
    """DELETE /projects/{id}/builds returns 400 when all builds are active."""
    mock_get_user.return_value = _USER
    mock_delete.side_effect = ValueError("No eligible builds to delete (active builds cannot be deleted)")

    resp = client.request(
        "DELETE",
        f"/projects/{_PROJECT_ID}/builds",
        headers=_auth_header(),
        json={"build_ids": [str(uuid.uuid4())]},
    )

    assert resp.status_code == 400
    assert "active builds" in resp.json()["detail"]
