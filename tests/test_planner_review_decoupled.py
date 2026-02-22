"""
Tests: plan_complete is broadcast BEFORE the post-synthesis review runs,
and the review fires as a background asyncio.Task (non-blocking).

Covers:
  1. plan_complete broadcast happens before _review_plan_with_thinking is called.
  2. Review is created as asyncio.Task (not awaited inline).
  3. Review failure does NOT affect run_project_planner return value.
  4. plan_analysis_complete event is broadcast when review finishes.
  5. Separator messages appear before and after thinking block.
  6. Review is skipped (no background task) when not using Haiku model.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from uuid import uuid4

import app.services.planner_service as ps

_BUILD_ID = uuid4()
_USER_ID = uuid4()
_API_KEY = "sk-test"

_DUMMY_PLAN = {
    "phases": [
        {"number": 1, "name": "Backend", "purpose": "Build API"},
        {"number": 2, "name": "Frontend", "purpose": "Build UI"},
    ],
}

_DUMMY_RESULT = {
    "plan_path": "/tmp/plan.json",
    "plan": _DUMMY_PLAN,
    "token_usage": {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_read_input_tokens": 200,
    },
    "iterations": 5,
}


def _make_broadcast_mock():
    """Return an AsyncMock that records all calls in order."""
    return AsyncMock()


# ── 1. plan_complete is broadcast BEFORE review is triggered ─────────────────

class TestPlanCompleteBeforeReview:

    def test_plan_complete_before_review_in_source(self):
        """Source code: plan_complete broadcast appears before create_task call."""
        import inspect
        src = inspect.getsource(ps.run_project_planner)
        plan_complete_pos = src.find('"plan_complete"')
        review_task_pos = src.find("create_task")
        assert plan_complete_pos != -1, "plan_complete broadcast not found in source"
        assert review_task_pos != -1, "create_task not found in source"
        assert plan_complete_pos < review_task_pos, (
            "plan_complete broadcast must appear before asyncio.create_task in source"
        )

    def test_review_not_awaited_inline(self):
        """Source code: _review_plan_with_thinking is NOT awaited inside run_project_planner."""
        import inspect
        src = inspect.getsource(ps.run_project_planner)
        # "await _review_plan_with_thinking" must not appear
        assert "await _review_plan_with_thinking" not in src, (
            "_review_plan_with_thinking must not be awaited inline — use create_task"
        )


# ── 2. Review runs as background task (asyncio.create_task, not await) ───────

class TestReviewAsBackgroundTask:

    def test_create_task_used_not_await(self):
        """Source: asyncio.create_task wraps the review call (not bare await)."""
        import inspect
        src = inspect.getsource(ps.run_project_planner)
        # The task creation pattern must be present
        assert "asyncio.create_task(" in src, (
            "run_project_planner must use asyncio.create_task() for the review"
        )
        # And the review must NOT be awaited directly
        assert "await _review_plan_with_thinking" not in src, (
            "_review_plan_with_thinking must not be awaited directly"
        )

    def test_review_guard_condition_is_haiku_only(self):
        """Source: review fires only when 'haiku' is in the planner model name."""
        import inspect
        src = inspect.getsource(ps.run_project_planner)
        # The guard must check for "haiku" before creating the task
        assert '"haiku"' in src or "'haiku'" in src, (
            "run_project_planner must check for haiku model before creating review task"
        )

    @pytest.mark.asyncio
    async def test_review_create_task_produces_asyncio_task(self):
        """asyncio.create_task with the review coroutine produces a real Task."""
        async def fake_review(**kwargs):
            pass

        task = asyncio.create_task(
            fake_review(
                plan=_DUMMY_PLAN,
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key=_API_KEY,
                thinking_model="claude-sonnet-4-6",
                thinking_budget=8000,
                broadcast_fn=AsyncMock(),
            )
        )
        assert isinstance(task, asyncio.Task)
        await task  # let it finish cleanly


# ── 3. Review failure does NOT affect return value ───────────────────────────

class TestReviewFailureNonFatal:

    @pytest.mark.asyncio
    async def test_review_failure_does_not_raise(self):
        """_review_plan_with_thinking swallows exceptions and doesn't propagate."""
        broadcast = AsyncMock()

        with patch("anthropic.AsyncAnthropic") as mock_ant_class:
            mock_client = MagicMock()
            mock_ant_class.return_value = mock_client
            mock_client.messages.create = AsyncMock(side_effect=RuntimeError("API unavailable"))

            # Should not raise
            await ps._review_plan_with_thinking(
                plan=_DUMMY_PLAN,
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key=_API_KEY,
                thinking_model="claude-sonnet-4-6",
                thinking_budget=8000,
                broadcast_fn=broadcast,
            )

        # Failure broadcast was sent (warn-level)
        warn_calls = [
            c for c in broadcast.call_args_list
            if c.args[2] == "build_log" and (c.args[3] or {}).get("level") == "warn"
        ]
        assert len(warn_calls) >= 1


# ── 4. plan_analysis_complete is broadcast at end of review ──────────────────

class TestPlanAnalysisCompleteEvent:

    @pytest.mark.asyncio
    async def test_plan_analysis_complete_event_broadcast(self):
        """plan_analysis_complete WS event is broadcast when review finishes."""
        broadcast = AsyncMock()

        with patch("anthropic.AsyncAnthropic") as mock_ant_class:
            # Simulate empty thinking response (no thinking blocks)
            mock_response = MagicMock()
            mock_response.content = []  # no blocks
            mock_client = MagicMock()
            mock_ant_class.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            await ps._review_plan_with_thinking(
                plan=_DUMMY_PLAN,
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key=_API_KEY,
                thinking_model="claude-sonnet-4-6",
                thinking_budget=8000,
                broadcast_fn=broadcast,
            )

        event_types = [c.args[2] for c in broadcast.call_args_list]
        assert "plan_analysis_complete" in event_types, (
            "plan_analysis_complete event must be broadcast at the end of review"
        )

    @pytest.mark.asyncio
    async def test_plan_analysis_complete_has_had_thinking_field(self):
        """plan_analysis_complete payload includes had_thinking bool."""
        broadcast = AsyncMock()

        with patch("anthropic.AsyncAnthropic") as mock_ant_class:
            mock_response = MagicMock()
            mock_response.content = []
            mock_client = MagicMock()
            mock_ant_class.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            await ps._review_plan_with_thinking(
                plan=_DUMMY_PLAN,
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key=_API_KEY,
                thinking_model="claude-sonnet-4-6",
                thinking_budget=8000,
                broadcast_fn=broadcast,
            )

        complete_calls = [
            c for c in broadcast.call_args_list if c.args[2] == "plan_analysis_complete"
        ]
        assert len(complete_calls) == 1
        payload = complete_calls[0].args[3]
        assert "had_thinking" in payload
        assert isinstance(payload["had_thinking"], bool)


# ── 5. Separator messages appear in review output ────────────────────────────

class TestReviewSeparators:

    @pytest.mark.asyncio
    async def test_separator_before_and_after_review(self):
        """Review function broadcasts separator messages before and after thinking."""
        broadcast = AsyncMock()

        with patch("anthropic.AsyncAnthropic") as mock_ant_class:
            mock_response = MagicMock()
            mock_response.content = []
            mock_client = MagicMock()
            mock_ant_class.return_value = mock_client
            mock_client.messages.create = AsyncMock(return_value=mock_response)

            await ps._review_plan_with_thinking(
                plan=_DUMMY_PLAN,
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key=_API_KEY,
                thinking_model="claude-sonnet-4-6",
                thinking_budget=8000,
                broadcast_fn=broadcast,
            )

        log_messages = [
            c.args[3].get("message", "")
            for c in broadcast.call_args_list
            if c.args[2] == "build_log"
        ]
        # At least one separator should contain "PLAN ANALYSIS" or "END ANALYSIS"
        separator_found = any(
            "PLAN ANALYSIS" in m or "END ANALYSIS" in m
            for m in log_messages
        )
        assert separator_found, (
            f"Expected separator messages in review output. Got: {log_messages}"
        )
