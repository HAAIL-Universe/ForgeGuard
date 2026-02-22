"""Tests for app/services/project/questionnaire.py.

Focuses on the testable pure functions and the async public API
(mocking LLM + repo layer):
  - _sections_for_mode
  - _system_prompt_for_mode
  - _questionnaire_progress
  - _parse_llm_response
  - process_questionnaire_message (ownership guard, already-complete path)
  - get_questionnaire_state
  - reset_questionnaire
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest


_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_project(**overrides) -> dict:
    base = {
        "id": str(_PROJECT_ID),
        "user_id": str(_USER_ID),
        "name": "Test Project",
        "description": "A test project",
        "status": "draft",
        "build_mode": "full",
        "questionnaire_state": {},
        "questionnaire_history": {},
    }
    return {**base, **overrides}


# ---------------------------------------------------------------------------
# _sections_for_mode
# ---------------------------------------------------------------------------

class TestSectionsForMode:
    def test_full_mode_returns_all_sections(self):
        from app.services.project.questionnaire import (
            _sections_for_mode,
            QUESTIONNAIRE_SECTIONS,
        )
        assert _sections_for_mode("full") == QUESTIONNAIRE_SECTIONS

    def test_mini_mode_returns_mini_sections(self):
        from app.services.project.questionnaire import (
            _sections_for_mode,
            MINI_QUESTIONNAIRE_SECTIONS,
        )
        assert _sections_for_mode("mini") == MINI_QUESTIONNAIRE_SECTIONS

    def test_unknown_mode_defaults_to_full(self):
        from app.services.project.questionnaire import (
            _sections_for_mode,
            QUESTIONNAIRE_SECTIONS,
        )
        assert _sections_for_mode("unknown_mode") == QUESTIONNAIRE_SECTIONS

    def test_full_sections_superset_of_mini(self):
        from app.services.project.questionnaire import (
            _sections_for_mode,
        )
        full = set(_sections_for_mode("full"))
        mini = set(_sections_for_mode("mini"))
        assert mini.issubset(full)

    def test_mini_sections_shorter_than_full(self):
        from app.services.project.questionnaire import _sections_for_mode
        assert len(_sections_for_mode("mini")) < len(_sections_for_mode("full"))


# ---------------------------------------------------------------------------
# _system_prompt_for_mode
# ---------------------------------------------------------------------------

class TestSystemPromptForMode:
    def test_full_mode_returns_nonempty_prompt(self):
        from app.services.project.questionnaire import _system_prompt_for_mode
        prompt = _system_prompt_for_mode("full")
        assert len(prompt.strip()) > 50

    def test_mini_mode_returns_nonempty_prompt(self):
        from app.services.project.questionnaire import _system_prompt_for_mode
        prompt = _system_prompt_for_mode("mini")
        assert len(prompt.strip()) > 50

    def test_mini_and_full_prompts_differ(self):
        from app.services.project.questionnaire import _system_prompt_for_mode
        assert _system_prompt_for_mode("full") != _system_prompt_for_mode("mini")

    def test_unknown_mode_returns_full_prompt(self):
        from app.services.project.questionnaire import (
            _system_prompt_for_mode,
            _SYSTEM_PROMPT,
        )
        assert _system_prompt_for_mode("anything") == _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# _questionnaire_progress
# ---------------------------------------------------------------------------

class TestQuestionnaireProgress:
    def test_empty_state_returns_first_section_as_current(self):
        from app.services.project.questionnaire import (
            _questionnaire_progress,
            QUESTIONNAIRE_SECTIONS,
        )
        result = _questionnaire_progress({})
        assert result["current_section"] == QUESTIONNAIRE_SECTIONS[0]
        assert result["is_complete"] is False
        assert result["completed_sections"] == []

    def test_all_completed_returns_is_complete(self):
        from app.services.project.questionnaire import (
            _questionnaire_progress,
            QUESTIONNAIRE_SECTIONS,
        )
        qs = {"completed_sections": list(QUESTIONNAIRE_SECTIONS)}
        result = _questionnaire_progress(qs)
        assert result["is_complete"] is True
        assert result["current_section"] is None
        assert result["remaining_sections"] == []

    def test_partial_completion_tracks_remaining(self):
        from app.services.project.questionnaire import (
            _questionnaire_progress,
            QUESTIONNAIRE_SECTIONS,
        )
        done = QUESTIONNAIRE_SECTIONS[:3]
        qs = {"completed_sections": done}
        result = _questionnaire_progress(qs)
        assert result["current_section"] == QUESTIONNAIRE_SECTIONS[3]
        assert len(result["remaining_sections"]) == len(QUESTIONNAIRE_SECTIONS) - 3

    def test_mini_mode_uses_mini_sections(self):
        from app.services.project.questionnaire import (
            _questionnaire_progress,
            MINI_QUESTIONNAIRE_SECTIONS,
        )
        result = _questionnaire_progress({}, build_mode="mini")
        assert result["current_section"] == MINI_QUESTIONNAIRE_SECTIONS[0]
        assert result["build_mode"] == "mini"

    def test_mini_mode_complete_after_mini_sections(self):
        from app.services.project.questionnaire import (
            _questionnaire_progress,
            MINI_QUESTIONNAIRE_SECTIONS,
        )
        qs = {"completed_sections": list(MINI_QUESTIONNAIRE_SECTIONS)}
        result = _questionnaire_progress(qs, build_mode="mini")
        assert result["is_complete"] is True


# ---------------------------------------------------------------------------
# _parse_llm_response
# ---------------------------------------------------------------------------

class TestParseLlmResponse:
    def test_parses_clean_json(self):
        from app.services.project.questionnaire import _parse_llm_response
        payload = {
            "reply": "Great, tell me about your stack.",
            "section": "product_intent",
            "section_complete": True,
            "extracted_data": {"name": "My App"},
        }
        result = _parse_llm_response(json.dumps(payload))
        assert result["reply"] == payload["reply"]
        assert result["section_complete"] is True

    def test_strips_markdown_fences(self):
        from app.services.project.questionnaire import _parse_llm_response
        payload = {"reply": "hi", "section": "x", "section_complete": False, "extracted_data": {}}
        raw = f"```json\n{json.dumps(payload)}\n```"
        result = _parse_llm_response(raw)
        assert result["reply"] == "hi"

    def test_strips_plain_code_fence(self):
        from app.services.project.questionnaire import _parse_llm_response
        payload = {"reply": "ok", "section": "x", "section_complete": False, "extracted_data": {}}
        raw = f"```\n{json.dumps(payload)}\n```"
        result = _parse_llm_response(raw)
        assert result["reply"] == "ok"

    def test_extracts_json_from_surrounding_text(self):
        from app.services.project.questionnaire import _parse_llm_response
        payload = {"reply": "extracted", "section": "x", "section_complete": False, "extracted_data": {}}
        raw = f"Here is my answer:\n{json.dumps(payload)}\nThat's all."
        result = _parse_llm_response(raw)
        assert result["reply"] == "extracted"

    def test_fallback_on_non_json(self):
        from app.services.project.questionnaire import _parse_llm_response
        raw = "This is plain text, not JSON at all."
        result = _parse_llm_response(raw)
        assert result["reply"] == raw
        assert result["section"] is None
        assert result["section_complete"] is False
        assert result["extracted_data"] is None

    def test_fallback_on_json_without_reply_key(self):
        from app.services.project.questionnaire import _parse_llm_response
        # Valid JSON but missing "reply" key — should fall back
        raw = json.dumps({"message": "hello", "status": "ok"})
        result = _parse_llm_response(raw)
        assert result["reply"] == raw
        assert result["section_complete"] is False

    def test_empty_string_falls_back(self):
        from app.services.project.questionnaire import _parse_llm_response
        result = _parse_llm_response("")
        assert isinstance(result["reply"], str)
        assert result["section_complete"] is False


# ---------------------------------------------------------------------------
# process_questionnaire_message — ownership guard + already-complete path
# ---------------------------------------------------------------------------

class TestProcessQuestionnaireMessage:
    @pytest.mark.asyncio
    async def test_raises_if_project_not_found(self):
        from app.services.project.questionnaire import process_questionnaire_message

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="Project not found"):
                await process_questionnaire_message(_USER_ID, _PROJECT_ID, "hello")

    @pytest.mark.asyncio
    async def test_raises_if_wrong_user(self):
        from app.services.project.questionnaire import process_questionnaire_message

        other_user = uuid.uuid4()
        project = _make_project(user_id=str(other_user))

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project):
            with pytest.raises(ValueError, match="Project not found"):
                await process_questionnaire_message(_USER_ID, _PROJECT_ID, "hello")

    @pytest.mark.asyncio
    async def test_returns_complete_message_when_all_sections_done(self):
        from app.services.project.questionnaire import (
            process_questionnaire_message,
            QUESTIONNAIRE_SECTIONS,
        )

        project = _make_project(
            questionnaire_state={"completed_sections": list(QUESTIONNAIRE_SECTIONS)},
        )

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project):
            result = await process_questionnaire_message(_USER_ID, _PROJECT_ID, "any message")

        assert result["section"] == "complete"
        assert result["is_complete"] is True

    @pytest.mark.asyncio
    async def test_calls_llm_and_updates_state(self):
        from app.services.project.questionnaire import process_questionnaire_message

        project = _make_project(build_mode="full")
        llm_payload = {
            "reply": "Tell me your stack.",
            "section": "product_intent",
            "section_complete": True,
            "extracted_data": {"intent": "A task manager"},
        }

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project), \
             patch("app.services.project.questionnaire.llm_chat", new_callable=AsyncMock, return_value={"text": json.dumps(llm_payload), "usage": {}}), \
             patch("app.services.project.questionnaire.update_questionnaire_state", new_callable=AsyncMock) as mock_state, \
             patch("app.services.project.questionnaire.update_questionnaire_history", new_callable=AsyncMock), \
             patch("app.services.project.questionnaire.update_project_status", new_callable=AsyncMock):

            result = await process_questionnaire_message(_USER_ID, _PROJECT_ID, "I want a task manager")

        assert result["reply"] == "Tell me your stack."
        mock_state.assert_awaited_once()


# ---------------------------------------------------------------------------
# get_questionnaire_state
# ---------------------------------------------------------------------------

class TestGetQuestionnaireState:
    @pytest.mark.asyncio
    async def test_raises_if_project_not_found(self):
        from app.services.project.questionnaire import get_questionnaire_state

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="Project not found"):
                await get_questionnaire_state(_USER_ID, _PROJECT_ID)

    @pytest.mark.asyncio
    async def test_raises_if_wrong_user(self):
        from app.services.project.questionnaire import get_questionnaire_state

        project = _make_project(user_id=str(uuid.uuid4()))
        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project):
            with pytest.raises(ValueError, match="Project not found"):
                await get_questionnaire_state(_USER_ID, _PROJECT_ID)

    @pytest.mark.asyncio
    async def test_returns_progress_for_valid_project(self):
        from app.services.project.questionnaire import get_questionnaire_state

        project = _make_project(
            questionnaire_state={"completed_sections": ["product_intent"]},
        )
        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project):
            result = await get_questionnaire_state(_USER_ID, _PROJECT_ID)

        assert "current_section" in result
        assert "completed_sections" in result
        assert "product_intent" in result["completed_sections"]


# ---------------------------------------------------------------------------
# reset_questionnaire
# ---------------------------------------------------------------------------

class TestResetQuestionnaire:
    @pytest.mark.asyncio
    async def test_raises_if_project_not_found(self):
        from app.services.project.questionnaire import reset_questionnaire

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=None):
            with pytest.raises(ValueError, match="Project not found"):
                await reset_questionnaire(_USER_ID, _PROJECT_ID)

    @pytest.mark.asyncio
    async def test_clears_state_and_history(self):
        from app.services.project.questionnaire import reset_questionnaire

        project = _make_project(
            questionnaire_state={"completed_sections": ["product_intent"]},
        )
        mock_state = AsyncMock()
        mock_history = AsyncMock()

        with patch("app.services.project.questionnaire.get_project_by_id", new_callable=AsyncMock, return_value=project), \
             patch("app.services.project.questionnaire.update_questionnaire_state", mock_state), \
             patch("app.services.project.questionnaire.update_questionnaire_history", mock_history):

            await reset_questionnaire(_USER_ID, _PROJECT_ID)

        # State should be reset to empty
        # reset_questionnaire calls update_questionnaire_state(project_id, {})
        mock_state.assert_awaited_once_with(_PROJECT_ID, {})
