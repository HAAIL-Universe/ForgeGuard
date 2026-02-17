"""Tests for project service -- questionnaire logic and contract generation."""

import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest

from app.services.project_service import (
    ContractCancelled,
    QUESTIONNAIRE_SECTIONS,
    _active_generations,
    _parse_llm_response,
    _questionnaire_progress,
    cancel_contract_generation,
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
    assert len(result["remaining_sections"]) == 7


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
    mock_llm.return_value = {
        "text": json.dumps({
            "reply": "Tell me about your product.",
            "section": "product_intent",
            "section_complete": False,
            "extracted_data": None,
        }),
        "usage": {"input_tokens": 50, "output_tokens": 30},
    }

    result = await process_questionnaire_message(USER_ID, PROJECT_ID, "Hi")

    assert result["reply"] == "Tell me about your product."
    assert result["is_complete"] is False
    assert result["token_usage"]["input_tokens"] == 50
    assert result["token_usage"]["output_tokens"] == 30
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
@patch("app.services.project_service._generate_contract_content", new_callable=AsyncMock)
@patch("app.services.project_service.manager.send_to_user", new_callable=AsyncMock)
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_generate_contracts_success(mock_project, mock_upsert, mock_status, mock_ws, mock_gen):
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
    mock_gen.return_value = ("# generated content", {"input_tokens": 100, "output_tokens": 200})

    result = await generate_contracts(USER_ID, PROJECT_ID)
    assert len(result) == 9
    assert mock_upsert.call_count == 9


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


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_cancel_contract_generation_no_active(mock_project):
    """Cancel raises when no generation is active."""
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    with pytest.raises(ValueError, match="No active"):
        await cancel_contract_generation(USER_ID, PROJECT_ID)


@pytest.mark.asyncio
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_cancel_contract_generation_success(mock_project):
    """Cancel sets the cancel event."""
    pid = str(PROJECT_ID)
    cancel_event = asyncio.Event()
    _active_generations[pid] = cancel_event
    mock_project.return_value = {
        "id": PROJECT_ID,
        "user_id": USER_ID,
        "name": "My Project",
        "description": None,
        "status": "contracts_ready",
    }
    result = await cancel_contract_generation(USER_ID, PROJECT_ID)
    assert result["status"] == "cancelling"
    assert cancel_event.is_set()
    # Clean up
    _active_generations.pop(pid, None)


@pytest.mark.asyncio
@patch("app.services.project_service._generate_contract_content", new_callable=AsyncMock)
@patch("app.services.project_service.manager.send_to_user", new_callable=AsyncMock)
@patch("app.services.project_service.update_project_status", new_callable=AsyncMock)
@patch("app.services.project_service.upsert_contract", new_callable=AsyncMock)
@patch("app.services.project_service.get_contracts_by_project", new_callable=AsyncMock, return_value=[])
@patch("app.services.project_service.get_project_by_id", new_callable=AsyncMock)
async def test_generate_contracts_cancelled_mid_generation(
    mock_project, mock_existing, mock_upsert, mock_status, mock_ws, mock_gen
):
    """Cancellation stops generation and raises ContractCancelled."""
    pid = str(PROJECT_ID)
    call_count = 0

    async def fake_gen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            # Simulate cancel being triggered after 3rd contract
            evt = _active_generations.get(pid)
            if evt:
                evt.set()
        return ("# content", {"input_tokens": 10, "output_tokens": 20})

    mock_gen.side_effect = fake_gen
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

    with pytest.raises(ContractCancelled):
        await generate_contracts(USER_ID, PROJECT_ID)

    # Cancel fires during the 3rd LLM call, so only 2 contracts are saved
    assert mock_upsert.call_count == 2


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
