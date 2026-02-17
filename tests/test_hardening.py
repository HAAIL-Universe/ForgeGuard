"""Tests for Phase 5 hardening: rate limiting, input validation, error handling."""

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app
from app.webhooks import _hmac_sha256


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")


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


def _sign(payload_bytes: bytes) -> str:
    digest = _hmac_sha256(b"whsec_test", payload_bytes)
    return f"sha256={digest}"


# ── Rate limiting ───────────────────────────────────────────────────────


@patch("app.api.routers.webhooks.process_push_event", new_callable=AsyncMock)
def test_webhook_rate_limit_blocks_excess(mock_process):
    """Webhook endpoint should return 429 when rate limit is exceeded."""
    from app.api.routers.webhooks import webhook_limiter

    # Reset limiter for test isolation
    webhook_limiter._hits.clear()

    mock_process.return_value = {"id": "test"}
    payload = json.dumps({
        "ref": "refs/heads/main",
        "head_commit": {"id": "abc", "message": "test", "author": {"name": "bot"}},
        "repository": {"id": 1, "full_name": "o/r"},
        "commits": [],
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(payload),
        "X-GitHub-Event": "push",
    }

    # Send up to the limit (30 requests)
    for _ in range(30):
        resp = client.post("/webhooks/github", content=payload, headers=headers)
        assert resp.status_code == 200

    # 31st request should be rate-limited
    resp = client.post("/webhooks/github", content=payload, headers=headers)
    assert resp.status_code == 429
    assert "rate limit" in resp.json()["detail"].lower()

    # Clean up
    webhook_limiter._hits.clear()


# ── Input validation ────────────────────────────────────────────────────
# Pydantic validation returns 422 BEFORE reaching the auth or DB layer,
# so we don't need to mock DB calls for these tests.


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
def test_connect_repo_rejects_invalid_full_name(mock_get_user):
    """full_name must match owner/repo pattern."""
    resp = client.post(
        "/repos/connect",
        json={
            "github_repo_id": 1,
            "full_name": "not a valid repo name!!!",
            "default_branch": "main",
        },
        headers=_auth_header(),
    )
    assert resp.status_code == 422


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
def test_connect_repo_rejects_zero_id(mock_get_user):
    """github_repo_id must be >= 1."""
    resp = client.post(
        "/repos/connect",
        json={
            "github_repo_id": 0,
            "full_name": "owner/repo",
            "default_branch": "main",
        },
        headers=_auth_header(),
    )
    assert resp.status_code == 422


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock, return_value=MOCK_USER)
def test_connect_repo_rejects_empty_branch(mock_get_user):
    """default_branch must not be empty."""
    resp = client.post(
        "/repos/connect",
        json={
            "github_repo_id": 1,
            "full_name": "owner/repo",
            "default_branch": "",
        },
        headers=_auth_header(),
    )
    assert resp.status_code == 422


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.api.routers.repos.connect_repo", new_callable=AsyncMock)
def test_connect_repo_accepts_valid_input(mock_connect, mock_get_user):
    """Valid input should pass validation and reach the service layer."""
    mock_get_user.return_value = MOCK_USER
    mock_connect.return_value = {
        "id": UUID("11111111-1111-1111-1111-111111111111"),
        "full_name": "owner/repo",
        "webhook_active": True,
    }
    resp = client.post(
        "/repos/connect",
        json={
            "github_repo_id": 12345,
            "full_name": "owner/repo",
            "default_branch": "main",
        },
        headers=_auth_header(),
    )
    assert resp.status_code == 200


# ── Error handling ──────────────────────────────────────────────────────


def test_global_error_handler_is_registered():
    """App should have a global exception handler for unhandled errors."""
    from app.main import app as test_app

    handlers = test_app.exception_handlers
    assert Exception in handlers


def test_cors_allows_valid_origin():
    """CORS should accept requests from the configured frontend origin."""
    # Must create a fresh app so the CORS middleware picks up the
    # monkeypatched FRONTEND_URL (module-level client was built before
    # the fixture ran).
    from app.main import create_app

    fresh = TestClient(create_app())
    resp = fresh.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
