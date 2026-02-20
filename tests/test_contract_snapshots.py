"""Tests for contract version history (snapshots) — Phase 31."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app

ALL_SECTIONS = [
    "product_intent", "tech_stack", "database_schema",
    "api_endpoints", "ui_requirements", "architectural_boundaries",
    "deployment_target",
]


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    """Set test configuration for all snapshot tests."""
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("app.config.settings.GITHUB_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("app.config.settings.FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("app.config.settings.APP_URL", "http://localhost:8000")
    monkeypatch.setattr("app.config.settings.GITHUB_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr("app.config.settings.ANTHROPIC_API_KEY", "test-api-key")
    monkeypatch.setattr("app.config.settings.LLM_QUESTIONNAIRE_MODEL", "claude-haiku-4-5")


USER_ID = "22222222-2222-2222-2222-222222222222"
OTHER_USER_ID = "33333333-3333-3333-3333-333333333333"
PROJECT_ID = "44444444-4444-4444-4444-444444444444"
MOCK_USER = {
    "id": UUID(USER_ID),
    "github_id": 99999,
    "github_login": "octocat",
    "avatar_url": "https://example.com/avatar.png",
    "access_token": "gho_testtoken123",
}
MOCK_PROJECT = {
    "id": UUID(PROJECT_ID),
    "user_id": UUID(USER_ID),
    "name": "Test Project",
    "description": "desc",
    "status": "contracts_ready",
    "repo_id": None,
    "questionnaire_state": {},
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
}

client = TestClient(app)


def _auth_header(uid=USER_ID):
    token = create_token(uid, "octocat")
    return {"Authorization": f"Bearer {token}"}


# ---------- GET /projects/{id}/contracts/history ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_batches", new_callable=AsyncMock)
def test_list_history_empty(mock_batches, mock_project, mock_get_user):
    """Empty history when no regenerations have occurred."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_batches.return_value = []

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_batches", new_callable=AsyncMock)
def test_list_history_with_batches(mock_batches, mock_project, mock_get_user):
    """Returns correct batch summaries."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_batches.return_value = [
        {"batch": 2, "created_at": "2025-06-01T12:00:00Z", "count": 9},
        {"batch": 1, "created_at": "2025-05-01T12:00:00Z", "count": 9},
    ]

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["batch"] == 2
    assert items[1]["batch"] == 1
    assert items[0]["count"] == 9


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
def test_list_history_project_not_found(mock_project, mock_get_user):
    """Returns 404 when project does not exist."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = None

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history",
        headers=_auth_header(),
    )
    assert resp.status_code == 404


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
def test_list_history_wrong_user(mock_project, mock_get_user):
    """Returns 404 when user does not own the project."""
    mock_get_user.return_value = {**MOCK_USER, "id": UUID(OTHER_USER_ID)}
    mock_project.return_value = MOCK_PROJECT

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history",
        headers=_auth_header(OTHER_USER_ID),
    )
    assert resp.status_code == 404


# ---------- GET /projects/{id}/contracts/history/{batch} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_contracts", new_callable=AsyncMock)
def test_get_batch_contracts(mock_snap, mock_project, mock_get_user):
    """Returns contract content for a specific batch."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_snap.return_value = [
        {
            "id": UUID("66666666-6666-6666-6666-666666666666"),
            "project_id": UUID(PROJECT_ID),
            "batch": 1,
            "contract_type": "blueprint",
            "content": "# Blueprint v1",
            "created_at": "2025-05-01T12:00:00Z",
        },
        {
            "id": UUID("77777777-7777-7777-7777-777777777777"),
            "project_id": UUID(PROJECT_ID),
            "batch": 1,
            "contract_type": "manifesto",
            "content": "# Manifesto v1",
            "created_at": "2025-05-01T12:00:00Z",
        },
    ]

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history/1",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert items[0]["contract_type"] == "blueprint"
    assert items[0]["content"] == "# Blueprint v1"
    assert items[0]["batch"] == 1
    assert items[1]["contract_type"] == "manifesto"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_contracts", new_callable=AsyncMock)
def test_get_batch_not_found(mock_snap, mock_project, mock_get_user):
    """Returns 404 when batch does not exist."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_snap.return_value = []

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history/999",
        headers=_auth_header(),
    )
    assert resp.status_code == 404


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
def test_get_batch_wrong_user(mock_project, mock_get_user):
    """Returns 404 when user does not own project."""
    mock_get_user.return_value = {**MOCK_USER, "id": UUID(OTHER_USER_ID)}
    mock_project.return_value = MOCK_PROJECT

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history/1",
        headers=_auth_header(OTHER_USER_ID),
    )
    assert resp.status_code == 404


# ---------- Snapshot logic (service layer) ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_batches", new_callable=AsyncMock)
def test_list_versions_delegates_to_repo(mock_batches, mock_project, mock_get_user):
    """list_contract_versions calls get_snapshot_batches with correct project_id."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_batches.return_value = [{"batch": 1, "created_at": "2025-01-01", "count": 9}]

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    mock_batches.assert_called_once_with(UUID(PROJECT_ID))


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project.get_snapshot_contracts", new_callable=AsyncMock)
def test_get_version_delegates_to_repo(mock_snap, mock_project, mock_get_user):
    """get_contract_version calls get_snapshot_contracts with correct args."""
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = MOCK_PROJECT
    mock_snap.return_value = [
        {
            "id": UUID("66666666-6666-6666-6666-666666666666"),
            "project_id": UUID(PROJECT_ID),
            "batch": 3,
            "contract_type": "blueprint",
            "content": "test",
            "created_at": "2025-01-01T00:00:00Z",
        },
    ]

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/history/3",
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    mock_snap.assert_called_once_with(UUID(PROJECT_ID), 3)


# ---------- Snapshot creation during generation ----------


@patch("app.services.project.contract_generator.llm_chat", new_callable=AsyncMock, return_value={"text": "[]", "usage": {"input_tokens": 0, "output_tokens": 0}})
@patch("app.services.project.contract_generator.manager")
@patch("app.services.project.contract_generator._generate_greenfield_contract_with_tools", new_callable=AsyncMock, return_value=("content", {"input_tokens": 100, "output_tokens": 200}))
@patch("app.services.project.contract_generator._generate_contract_content", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.update_project_status", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.snapshot_contracts", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.get_contracts_by_project", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.get_project_by_id", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_snapshots_before_regeneration(
    mock_project, mock_existing, mock_snapshot,
    mock_upsert, mock_status, mock_gen, mock_greenfield_gen, mock_manager,
    mock_llm_chat,
):
    """When ALL contracts already exist (full set), generate_contracts snapshots them first."""
    from app.services.project.contract_generator import CONTRACT_TYPES
    from app.services.project_service import generate_contracts

    mock_project.return_value = {
        **MOCK_PROJECT,
        "questionnaire_state": {
            "completed_sections": ALL_SECTIONS,
            "answers": {"product_intent": [{"q": "test", "a": "test"}]},
        },
    }
    # All 9 contracts exist → should trigger snapshot then regenerate
    mock_existing.return_value = [
        {"id": UUID("66666666-6666-6666-6666-666666666666"),
         "project_id": UUID(PROJECT_ID),
         "contract_type": ct, "content": "old",
         "version": 1, "created_at": "2025-01-01T00:00:00Z",
         "updated_at": "2025-01-01T00:00:00Z"}
        for ct in CONTRACT_TYPES
    ]
    mock_snapshot.return_value = 1

    # Each contract generation
    mock_gen.return_value = ("content", {"input_tokens": 100, "output_tokens": 200})
    mock_upsert.return_value = {
        "id": UUID("66666666-6666-6666-6666-666666666666"),
        "project_id": UUID(PROJECT_ID),
        "contract_type": "blueprint",
        "version": 2,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }
    mock_manager.send_to_user = AsyncMock()

    result = await generate_contracts(UUID(USER_ID), UUID(PROJECT_ID))
    assert isinstance(result, list)
    mock_snapshot.assert_called_once_with(UUID(PROJECT_ID))


@patch("app.services.project.contract_generator.llm_chat", new_callable=AsyncMock, return_value={"text": "[]", "usage": {"input_tokens": 0, "output_tokens": 0}})
@patch("app.services.project.contract_generator.manager")
@patch("app.services.project.contract_generator._generate_greenfield_contract_with_tools", new_callable=AsyncMock, return_value=("content", {"input_tokens": 100, "output_tokens": 200}))
@patch("app.services.project.contract_generator._generate_contract_content", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.update_project_status", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.snapshot_contracts", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.get_contracts_by_project", new_callable=AsyncMock)
@patch("app.services.project.contract_generator.get_project_by_id", new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_generate_no_snapshot_for_first_generation(
    mock_project, mock_existing, mock_snapshot,
    mock_upsert, mock_status, mock_gen, mock_greenfield_gen, mock_manager,
    mock_llm_chat,
):
    """First generation (no existing contracts) does not create a snapshot."""
    from app.services.project_service import generate_contracts

    mock_project.return_value = {
        **MOCK_PROJECT,
        "questionnaire_state": {
            "completed_sections": ALL_SECTIONS,
            "answers": {"product_intent": [{"q": "test", "a": "test"}]},
        },
    }
    # No existing contracts
    mock_existing.return_value = []
    mock_gen.return_value = ("content", {"input_tokens": 100, "output_tokens": 200})
    mock_upsert.return_value = {
        "id": UUID("66666666-6666-6666-6666-666666666666"),
        "project_id": UUID(PROJECT_ID),
        "contract_type": "blueprint",
        "version": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }
    mock_manager.send_to_user = AsyncMock()

    result = await generate_contracts(UUID(USER_ID), UUID(PROJECT_ID))
    assert isinstance(result, list)
    mock_snapshot.assert_not_called()


# ---------- No auth ----------


def test_history_requires_auth():
    """History endpoints require authentication."""
    resp = client.get(f"/projects/{PROJECT_ID}/contracts/history")
    assert resp.status_code == 401

    resp = client.get(f"/projects/{PROJECT_ID}/contracts/history/1")
    assert resp.status_code == 401


