"""Tests for webhook and audit API endpoints."""

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


USER_ID = "22222222-2222-2222-2222-222222222222"
REPO_ID = "33333333-3333-3333-3333-333333333333"
AUDIT_ID = "44444444-4444-4444-4444-444444444444"
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


# ---------- POST /webhooks/github ----------


@patch("app.api.routers.webhooks.process_push_event", new_callable=AsyncMock)
def test_webhook_accepts_valid_push(mock_process):
    mock_process.return_value = {"id": AUDIT_ID}
    payload = json.dumps({
        "ref": "refs/heads/main",
        "head_commit": {"id": "abc1234", "message": "test", "author": {"name": "bot"}},
        "repository": {"id": 12345, "full_name": "octocat/hello"},
        "commits": [],
    }).encode()

    response = client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(payload),
            "X-GitHub-Event": "push",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"


def test_webhook_rejects_invalid_signature():
    payload = b'{"ref": "refs/heads/main"}'
    response = client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=invalid",
            "X-GitHub-Event": "push",
        },
    )
    assert response.status_code == 401


def test_webhook_ignores_non_push_events():
    payload = json.dumps({"action": "opened"}).encode()
    response = client.post(
        "/webhooks/github",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _sign(payload),
            "X-GitHub-Event": "pull_request",
        },
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"


# ---------- GET /repos/{id}/audits ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.repos.repo_repo.get_repo_by_id", new_callable=AsyncMock)
@patch("app.repos.audit_repo.get_audit_runs_by_repo", new_callable=AsyncMock)
def test_list_audits(mock_runs, mock_repo, mock_user):
    mock_user.return_value = MOCK_USER
    mock_repo.return_value = {"id": UUID(REPO_ID), "user_id": UUID(USER_ID)}
    mock_runs.return_value = ([], 0)

    response = client.get(f"/repos/{REPO_ID}/audits", headers=_auth_header())
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_list_audits_requires_auth():
    response = client.get(f"/repos/{REPO_ID}/audits")
    assert response.status_code == 401


# ---------- GET /repos/{id}/audits/{aid} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.repos.repo_repo.get_repo_by_id", new_callable=AsyncMock)
@patch("app.repos.audit_repo.get_audit_run_detail", new_callable=AsyncMock)
def test_audit_detail_not_found(mock_detail, mock_repo, mock_user):
    mock_user.return_value = MOCK_USER
    mock_repo.return_value = {"id": UUID(REPO_ID), "user_id": UUID(USER_ID)}
    mock_detail.return_value = None

    response = client.get(f"/repos/{REPO_ID}/audits/{AUDIT_ID}", headers=_auth_header())
    assert response.status_code == 404
