"""Tests for projects router endpoints."""

from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.auth import create_token
from app.main import app


@pytest.fixture(autouse=True)
def _set_test_config(monkeypatch):
    """Set test configuration for all projects router tests."""
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
PROJECT_ID = "44444444-4444-4444-4444-444444444444"
CONTRACT_ID = "55555555-5555-5555-5555-555555555555"
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


# ---------- POST /projects ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
def test_create_project(mock_create, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_create.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": "A test project",
        "status": "draft",
        "repo_id": None,
        "questionnaire_state": {},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    resp = client.post(
        "/projects",
        json={"name": "My Project", "description": "A test project"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "My Project"
    assert data["status"] == "draft"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
def test_create_project_with_repo_id(mock_create, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_create.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "Linked Project",
        "description": None,
        "status": "draft",
        "repo_id": UUID("33333333-3333-3333-3333-333333333333"),
        "local_path": None,
        "questionnaire_state": {},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    resp = client.post(
        "/projects",
        json={"name": "Linked Project", "repo_id": "33333333-3333-3333-3333-333333333333"},
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    mock_create.assert_called_once()
    call_kwargs = mock_create.call_args
    assert call_kwargs.kwargs.get("repo_id") == UUID("33333333-3333-3333-3333-333333333333") or \
           call_kwargs[1].get("repo_id") == UUID("33333333-3333-3333-3333-333333333333") or \
           (len(call_kwargs.args) > 3 and call_kwargs.args[3] is not None)


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
def test_create_project_with_local_path(mock_create, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_create.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "Local Project",
        "description": None,
        "status": "draft",
        "repo_id": None,
        "local_path": "C:\\Users\\me\\projects\\app",
        "questionnaire_state": {},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    resp = client.post(
        "/projects",
        json={"name": "Local Project", "local_path": "C:\\Users\\me\\projects\\app"},
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Local Project"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_create_project_missing_name(mock_get_user):
    mock_get_user.return_value = MOCK_USER
    resp = client.post("/projects", json={}, headers=_auth_header())
    assert resp.status_code == 422


def test_create_project_unauthenticated():
    resp = client.post("/projects", json={"name": "Test"})
    assert resp.status_code == 401


# ---------- GET /projects ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_projects_by_user", new_callable=AsyncMock)
def test_list_projects(mock_list, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_list.return_value = [
        {
            "id": UUID(PROJECT_ID),
            "user_id": UUID(USER_ID),
            "name": "My Project",
            "description": None,
            "status": "draft",
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
    ]

    resp = client.get("/projects", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "My Project"


# ---------- GET /projects/{id} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock)
@patch("app.services.project_service.build_repo.get_latest_build_for_project", new_callable=AsyncMock)
def test_get_project_detail(mock_latest_build, mock_contracts, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": "Desc",
        "status": "draft",
        "repo_id": None,
        "questionnaire_state": {},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }
    mock_contracts.return_value = []
    mock_latest_build.return_value = None

    resp = client.get(f"/projects/{PROJECT_ID}", headers=_auth_header())
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "My Project"
    assert data["questionnaire_progress"]["is_complete"] is False


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
def test_get_project_not_found(mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = None

    resp = client.get(f"/projects/{PROJECT_ID}", headers=_auth_header())
    assert resp.status_code == 404


# ---------- DELETE /projects/{id} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.repo_delete_project", new_callable=AsyncMock)
@patch("app.repos.build_repo.has_active_builds", new_callable=AsyncMock)
def test_delete_project(mock_active, mock_delete, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_active.return_value = False
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "draft",
    }
    mock_delete.return_value = True

    resp = client.delete(f"/projects/{PROJECT_ID}", headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


# ---------- POST /projects/{id}/questionnaire ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.update_questionnaire_state", new_callable=AsyncMock)
@patch("app.services.project_service.llm_chat", new_callable=AsyncMock)
def test_questionnaire_message(
    mock_llm, mock_update_qs, mock_update_status, mock_project, mock_get_user
):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": "A test",
        "status": "draft",
        "questionnaire_state": {},
    }
    mock_llm.return_value = {
        "text": '{"reply": "What does your product do?", "section": "product_intent", "section_complete": false, "extracted_data": null}',
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }

    resp = client.post(
        f"/projects/{PROJECT_ID}/questionnaire",
        json={"message": "I want to build an app"},
        headers=_auth_header(),
    )

    assert resp.status_code == 200
    data = resp.json()
    assert "reply" in data
    assert data["is_complete"] is False


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
def test_questionnaire_project_not_found(mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = None

    resp = client.post(
        f"/projects/{PROJECT_ID}/questionnaire",
        json={"message": "hello"},
        headers=_auth_header(),
    )
    assert resp.status_code == 404


# ---------- GET /projects/{id}/questionnaire/state ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
def test_questionnaire_state(mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "questionnaire",
        "questionnaire_state": {
            "completed_sections": ["product_intent"],
            "answers": {"product_intent": {"intent": "build an app"}},
        },
    }

    resp = client.get(
        f"/projects/{PROJECT_ID}/questionnaire/state", headers=_auth_header()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["completed_sections"] == ["product_intent"]
    assert data["current_section"] == "tech_stack"
    assert data["is_complete"] is False


# ---------- POST /projects/{id}/contracts/generate ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
def test_generate_contracts_incomplete(mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "questionnaire",
        "questionnaire_state": {"completed_sections": ["product_intent"]},
    }

    resp = client.post(
        f"/projects/{PROJECT_ID}/contracts/generate", headers=_auth_header()
    )
    assert resp.status_code == 400
    assert "not complete" in resp.json()["detail"].lower()


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.manager.send_to_user", new_callable=AsyncMock)
@patch("app.services.project_service._generate_contract_content", new_callable=AsyncMock)
@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock, return_value=[])
def test_generate_contracts_success(
    mock_existing, mock_gen, mock_ws, mock_status, mock_upsert, mock_project, mock_get_user
):
    mock_get_user.return_value = MOCK_USER
    mock_gen.return_value = ("# generated content", {"input_tokens": 100, "output_tokens": 200})
    all_sections = [
        "product_intent", "tech_stack", "database_schema", "api_endpoints",
        "ui_requirements", "architectural_boundaries", "deployment_target",
        "phase_breakdown",
    ]
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": "A test",
        "status": "contracts_ready",
        "questionnaire_state": {
            "completed_sections": all_sections,
            "answers": {s: {"key": "value"} for s in all_sections},
        },
    }
    mock_upsert.return_value = {
        "id": UUID(CONTRACT_ID),
        "project_id": UUID(PROJECT_ID),
        "contract_type": "blueprint",
        "content": "# content",
        "version": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    resp = client.post(
        f"/projects/{PROJECT_ID}/contracts/generate", headers=_auth_header()
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["contracts"]) == 9


# ---------- POST /projects/{id}/contracts/cancel ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
def test_cancel_contracts_no_active(mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }

    resp = client.post(
        f"/projects/{PROJECT_ID}/contracts/cancel", headers=_auth_header()
    )
    assert resp.status_code == 400
    assert "no active" in resp.json()["detail"].lower()


# ---------- GET /projects/{id}/contracts ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock)
def test_list_contracts(mock_contracts, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    mock_contracts.return_value = [
        {
            "id": UUID(CONTRACT_ID),
            "project_id": UUID(PROJECT_ID),
            "contract_type": "blueprint",
            "content": "# content",
            "version": 1,
            "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-01T00:00:00Z",
        }
    ]

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts", headers=_auth_header()
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


# ---------- GET /projects/{id}/contracts/{type} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_contract_by_type", new_callable=AsyncMock)
def test_get_contract(mock_contract, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    mock_contract.return_value = {
        "id": UUID(CONTRACT_ID),
        "project_id": UUID(PROJECT_ID),
        "contract_type": "blueprint",
        "content": "# My Blueprint",
        "version": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/blueprint", headers=_auth_header()
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "# My Blueprint"


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_contract_by_type", new_callable=AsyncMock)
def test_get_contract_not_found(mock_contract, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    mock_contract.return_value = None

    resp = client.get(
        f"/projects/{PROJECT_ID}/contracts/blueprint", headers=_auth_header()
    )
    assert resp.status_code == 404


# ---------- PUT /projects/{id}/contracts/{type} ----------


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
@patch(
    "app.services.project_service.repo_update_contract_content",
    new_callable=AsyncMock,
)
def test_update_contract(mock_update, mock_project, mock_get_user):
    mock_get_user.return_value = MOCK_USER
    mock_project.return_value = {
        "id": UUID(PROJECT_ID),
        "user_id": UUID(USER_ID),
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    mock_update.return_value = {
        "id": UUID(CONTRACT_ID),
        "project_id": UUID(PROJECT_ID),
        "contract_type": "blueprint",
        "content": "# Updated",
        "version": 2,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-02T00:00:00Z",
    }

    resp = client.put(
        f"/projects/{PROJECT_ID}/contracts/blueprint",
        json={"content": "# Updated"},
        headers=_auth_header(),
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


@patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
def test_update_contract_invalid_type(mock_get_user):
    mock_get_user.return_value = MOCK_USER

    resp = client.put(
        f"/projects/{PROJECT_ID}/contracts/invalid_type",
        json={"content": "# test"},
        headers=_auth_header(),
    )
    assert resp.status_code == 400
