"""Tests for project service -- questionnaire logic and contract generation."""

import json
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.services.project_service import (
    QUESTIONNAIRE_SECTIONS,
    _build_template_vars,
    _parse_llm_response,
    _questionnaire_progress,
    _render_template,
    create_new_project,
    generate_contracts,
    get_project_detail,
    get_questionnaire_state,
    list_user_projects,
    process_questionnaire_message,
    update_contract,
)


USER_ID = UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = UUID("44444444-4444-4444-4444-444444444444")


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------


def test_parse_valid_json():
    raw = '{"reply": "Hello!", "section": "product_intent", "section_complete": false, "extracted_data": null}'
    result = _parse_llm_response(raw)
    assert result["reply"] == "Hello!"
    assert result["section"] == "product_intent"
    assert result["section_complete"] is False


def test_parse_json_with_code_fences():
    raw = '```json\n{"reply": "Hi", "section": "tech_stack", "section_complete": true, "extracted_data": {"lang": "python"}}\n```'
    result = _parse_llm_response(raw)
    assert result["reply"] == "Hi"
    assert result["section_complete"] is True
    assert result["extracted_data"]["lang"] == "python"


def test_parse_fallback_plain_text():
    raw = "I'm just a plain text response without JSON."
    result = _parse_llm_response(raw)
    assert result["reply"] == raw
    assert result["section_complete"] is False


def test_parse_invalid_json():
    raw = '{"reply": "missing closing brace'
    result = _parse_llm_response(raw)
    assert result["reply"] == raw
    assert result["section_complete"] is False


# ---------------------------------------------------------------------------
# _questionnaire_progress
# ---------------------------------------------------------------------------


def test_progress_empty():
    result = _questionnaire_progress({})
    assert result["current_section"] == "product_intent"
    assert result["completed_sections"] == []
    assert result["is_complete"] is False
    assert len(result["remaining_sections"]) == 8


def test_progress_partial():
    result = _questionnaire_progress(
        {"completed_sections": ["product_intent", "tech_stack"]}
    )
    assert result["current_section"] == "database_schema"
    assert len(result["completed_sections"]) == 2
    assert result["is_complete"] is False


def test_progress_complete():
    result = _questionnaire_progress(
        {"completed_sections": list(QUESTIONNAIRE_SECTIONS)}
    )
    assert result["current_section"] is None
    assert result["is_complete"] is True
    assert len(result["remaining_sections"]) == 0


# ---------------------------------------------------------------------------
# _build_template_vars
# ---------------------------------------------------------------------------


def test_build_template_vars():
    project = {"name": "TestApp", "description": "A test app"}
    answers = {
        "product_intent": {"product_intent": "Build a dashboard", "target_users": "devs"},
        "tech_stack": {"backend_language": "Python"},
    }
    result = _build_template_vars(project, answers)
    assert result["project_name"] == "TestApp"
    assert result["product_intent"] == "Build a dashboard"
    assert result["backend_language"] == "Python"


def test_build_template_vars_with_list():
    project = {"name": "TestApp", "description": ""}
    answers = {"product_intent": {"key_features": ["auth", "dashboard", "api"]}}
    result = _build_template_vars(project, answers)
    assert "- auth" in result["key_features"]
    assert "- dashboard" in result["key_features"]


# ---------------------------------------------------------------------------
# _render_template
# ---------------------------------------------------------------------------


def test_render_template_blueprint():
    variables = {
        "project_name": "TestApp",
        "project_description": "A test app",
        "product_intent": "Build something",
        "target_users": "developers",
        "key_features": "- feature1",
        "success_criteria": "works",
    }
    result = _render_template("blueprint", variables)
    assert "TestApp" in result
    assert "A test app" in result
    assert "Build something" in result


def test_render_template_missing_vars():
    variables = {"project_name": "TestApp"}
    result = _render_template("blueprint", variables)
    assert "TestApp" in result
    # Missing vars should become empty strings, not raise
    assert "{" not in result


# ---------------------------------------------------------------------------
# create_new_project (async, mocked repo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.project_service.repo_create_project", new_callable=AsyncMock)
async def test_create_new_project(mock_create):
    mock_create.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": None,
        "status": "draft",
        "repo_id": None,
        "questionnaire_state": {},
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    result = await create_new_project(USER_ID, "My Project")
    assert result["name"] == "My Project"
    mock_create.assert_called_once_with(USER_ID, "My Project", None, repo_id=None, local_path=None)


# ---------------------------------------------------------------------------
# process_questionnaire_message (async, mocked deps)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.project_service.update_questionnaire_state", new_callable=AsyncMock)
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.llm_chat", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_process_questionnaire_first_message(
    mock_project, mock_llm, mock_status, mock_qs
):
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": "test",
        "status": "draft",
        "questionnaire_state": {},
    }
    mock_llm.return_value = json.dumps({
        "reply": "Tell me about your product.",
        "section": "product_intent",
        "section_complete": False,
        "extracted_data": None,
    })

    result = await process_questionnaire_message(USER_ID, PROJECT_ID, "Hi")

    assert result["reply"] == "Tell me about your product."
    assert result["is_complete"] is False
    mock_status.assert_called_once()  # draft -> questionnaire


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_process_questionnaire_not_found(mock_project):
    mock_project.return_value = None

    with pytest.raises(ValueError, match="not found"):
        await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_process_questionnaire_wrong_user(mock_project):
    other_user = UUID("99999999-9999-9999-9999-999999999999")
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": other_user,
        "name": "Not mine",
        "description": None,
        "status": "draft",
        "questionnaire_state": {},
    }

    with pytest.raises(ValueError, match="not found"):
        await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_process_questionnaire_already_complete(mock_project):
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
        "questionnaire_state": {
            "completed_sections": list(QUESTIONNAIRE_SECTIONS),
        },
    }

    result = await process_questionnaire_message(USER_ID, PROJECT_ID, "hello")
    assert result["is_complete"] is True


# ---------------------------------------------------------------------------
# generate_contracts (async, mocked deps)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_generate_contracts_success(mock_project, mock_upsert, mock_status):
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": "A test",
        "status": "contracts_ready",
        "questionnaire_state": {
            "completed_sections": list(QUESTIONNAIRE_SECTIONS),
            "answers": {s: {"key": "value"} for s in QUESTIONNAIRE_SECTIONS},
        },
    }
    mock_upsert.return_value = {
        "id": UUID("55555555-5555-5555-5555-555555555555"),
        "project_id": PROJECT_ID,
        "contract_type": "blueprint",
        "content": "# content",
        "version": 1,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    }

    result = await generate_contracts(USER_ID, PROJECT_ID)
    assert len(result) == 10
    assert mock_upsert.call_count == 10


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_generate_contracts_incomplete(mock_project):
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": None,
        "status": "questionnaire",
        "questionnaire_state": {"completed_sections": ["product_intent"]},
    }

    with pytest.raises(ValueError, match="not complete"):
        await generate_contracts(USER_ID, PROJECT_ID)


# ---------------------------------------------------------------------------
# get_questionnaire_state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_get_questionnaire_state(mock_project):
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "P",
        "description": None,
        "status": "questionnaire",
        "questionnaire_state": {
            "completed_sections": ["product_intent", "tech_stack"],
        },
    }

    result = await get_questionnaire_state(USER_ID, PROJECT_ID)
    assert result["current_section"] == "database_schema"
    assert result["is_complete"] is False


# ---------------------------------------------------------------------------
# update_contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_contract_invalid_type():
    with pytest.raises(ValueError, match="Invalid contract type"):
        await update_contract(USER_ID, PROJECT_ID, "not_a_type", "content")
