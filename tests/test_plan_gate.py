"""Tests for Phase 42: Plan Confirmation Gate, IDE Ready Gate, Cost Estimation,
and Sub-Agent Activity Broadcasts.

Covers:
- _state.py plan-review helpers (register, resolve, pop, cleanup)
- _state.py IDE-ready helpers (register, resolve, pop, cleanup)
- cost.py estimate_phase_cost()
- build_service.py approve_plan()
- build_service.py commence_build()
- subagent.py subagent_activity broadcasts
- builds.py router endpoints (approve-plan, commence)
"""

import asyncio
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.build._state import (
    _plan_review_events,
    _plan_review_responses,
    _ide_ready_events,
    _ide_ready_responses,
    register_plan_review,
    resolve_plan_review,
    pop_plan_review_response,
    cleanup_plan_review,
    register_ide_ready,
    resolve_ide_ready,
    pop_ide_ready_response,
    cleanup_ide_ready,
)
from app.services.build.cost import estimate_phase_cost
from app.services import build_service


_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_gate_state():
    """Ensure gate state is clean before/after each test."""
    bid = str(_BUILD_ID)
    yield
    _plan_review_events.pop(bid, None)
    _plan_review_responses.pop(bid, None)
    _ide_ready_events.pop(bid, None)
    _ide_ready_responses.pop(bid, None)


@pytest.fixture(autouse=True)
def _mock_build_state_deps():
    mock_repo = MagicMock()
    for m in ("append_build_log", "record_build_cost", "update_build",
              "update_build_status", "get_build", "pause_build"):
        setattr(mock_repo, m, AsyncMock(return_value=None))
    mock_mgr = MagicMock()
    mock_mgr.send_to_user = AsyncMock(return_value=None)
    mock_mgr.send_to_group = AsyncMock(return_value=None)
    with patch("app.services.build._state.build_repo", mock_repo), \
         patch("app.services.build._state.manager", mock_mgr):
        yield {"build_repo": mock_repo, "manager": mock_mgr}


# ===========================================================================
# Plan-review state helpers
# ===========================================================================


class TestPlanReviewState:
    """Test register/resolve/pop/cleanup for plan_review gate."""

    def test_register_creates_event(self):
        event = register_plan_review(str(_BUILD_ID))
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()
        assert str(_BUILD_ID) in _plan_review_events

    def test_resolve_sets_event_and_stores_response(self):
        event = register_plan_review(str(_BUILD_ID))
        result = resolve_plan_review(str(_BUILD_ID), {"action": "approve"})
        assert result is True
        assert event.is_set()
        assert _plan_review_responses[str(_BUILD_ID)] == {"action": "approve"}

    def test_resolve_returns_false_if_no_pending(self):
        result = resolve_plan_review(str(_BUILD_ID), {"action": "approve"})
        assert result is False

    def test_pop_returns_response_and_clears(self):
        register_plan_review(str(_BUILD_ID))
        resolve_plan_review(str(_BUILD_ID), {"action": "reject"})
        resp = pop_plan_review_response(str(_BUILD_ID))
        assert resp == {"action": "reject"}
        assert str(_BUILD_ID) not in _plan_review_events
        assert str(_BUILD_ID) not in _plan_review_responses

    def test_pop_returns_none_if_no_pending(self):
        assert pop_plan_review_response(str(_BUILD_ID)) is None

    def test_cleanup_removes_all_state(self):
        register_plan_review(str(_BUILD_ID))
        resolve_plan_review(str(_BUILD_ID), {"action": "approve"})
        cleanup_plan_review(str(_BUILD_ID))
        assert str(_BUILD_ID) not in _plan_review_events
        assert str(_BUILD_ID) not in _plan_review_responses


# ===========================================================================
# IDE-ready state helpers
# ===========================================================================


class TestIDEReadyState:
    """Test register/resolve/pop/cleanup for ide_ready gate."""

    def test_register_creates_event(self):
        event = register_ide_ready(str(_BUILD_ID))
        assert isinstance(event, asyncio.Event)
        assert not event.is_set()
        assert str(_BUILD_ID) in _ide_ready_events

    def test_resolve_sets_event_and_stores_response(self):
        event = register_ide_ready(str(_BUILD_ID))
        result = resolve_ide_ready(str(_BUILD_ID), {"action": "commence"})
        assert result is True
        assert event.is_set()
        assert _ide_ready_responses[str(_BUILD_ID)] == {"action": "commence"}

    def test_resolve_returns_false_if_no_pending(self):
        result = resolve_ide_ready(str(_BUILD_ID), {"action": "commence"})
        assert result is False

    def test_pop_returns_response_and_clears(self):
        register_ide_ready(str(_BUILD_ID))
        resolve_ide_ready(str(_BUILD_ID), {"action": "cancel"})
        resp = pop_ide_ready_response(str(_BUILD_ID))
        assert resp == {"action": "cancel"}
        assert str(_BUILD_ID) not in _ide_ready_events
        assert str(_BUILD_ID) not in _ide_ready_responses

    def test_pop_returns_none_if_no_pending(self):
        assert pop_ide_ready_response(str(_BUILD_ID)) is None

    def test_cleanup_removes_all_state(self):
        register_ide_ready(str(_BUILD_ID))
        resolve_ide_ready(str(_BUILD_ID), {"action": "commence"})
        cleanup_ide_ready(str(_BUILD_ID))
        assert str(_BUILD_ID) not in _ide_ready_events
        assert str(_BUILD_ID) not in _ide_ready_responses


# ===========================================================================
# Cost estimation
# ===========================================================================


class TestEstimatePhaseCost:
    """Test estimate_phase_cost returns sensible estimates."""

    def _make_manifest(self, n=5, lines_each=100):
        return [
            {"path": f"src/file_{i}.py", "purpose": f"File {i}",
             "estimated_lines": lines_each, "language": "python"}
            for i in range(n)
        ]

    def _make_chunks(self, n=2):
        return [
            {"name": f"Chunk {i}", "files": [f"src/file_{i}.py"]}
            for i in range(n)
        ]

    def test_basic_estimate_structure(self):
        manifest = self._make_manifest(5, 100)
        chunks = self._make_chunks(2)
        est = estimate_phase_cost(manifest, chunks)

        assert "files" in est
        assert "estimated_lines" in est
        assert "chunks" in est
        assert "estimated_cost_low_usd" in est
        assert "estimated_cost_high_usd" in est
        assert "spent_so_far_usd" in est
        assert "spend_cap_usd" in est
        assert "remaining_budget_usd" in est
        assert "breakdown" in est

    def test_file_count_matches(self):
        manifest = self._make_manifest(8, 50)
        chunks = self._make_chunks(3)
        est = estimate_phase_cost(manifest, chunks)
        assert est["files"] == 8
        assert est["estimated_lines"] == 400
        assert est["chunks"] == 3

    def test_high_exceeds_low(self):
        manifest = self._make_manifest(5, 100)
        chunks = self._make_chunks(2)
        est = estimate_phase_cost(manifest, chunks)
        assert est["estimated_cost_high_usd"] > est["estimated_cost_low_usd"]

    def test_spent_so_far_reduces_remaining(self, monkeypatch):
        monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 10.0)
        manifest = self._make_manifest(5, 100)
        chunks = self._make_chunks(2)
        est = estimate_phase_cost(manifest, chunks, spent_so_far=3.0, spend_cap=10.0)
        assert est["spent_so_far_usd"] == 3.0
        assert est["remaining_budget_usd"] == 7.0

    def test_no_spend_cap_returns_none_remaining(self, monkeypatch):
        monkeypatch.setattr("app.services.build._state.settings.BUILD_MAX_COST_USD", 0)
        manifest = self._make_manifest(3, 50)
        chunks = self._make_chunks(1)
        est = estimate_phase_cost(manifest, chunks, spend_cap=None)
        assert est["remaining_budget_usd"] is None

    def test_empty_manifest(self):
        est = estimate_phase_cost([], [])
        assert est["files"] == 0
        assert est["estimated_cost_low_usd"] == 0.0

    def test_breakdown_has_three_components(self):
        manifest = self._make_manifest(5, 100)
        chunks = self._make_chunks(2)
        est = estimate_phase_cost(manifest, chunks)
        breakdown = est["breakdown"]
        assert "coder_opus" in breakdown
        assert "planning_sonnet" in breakdown
        assert "audit_sonnet" in breakdown
        assert all(v >= 0 for v in breakdown.values())


# ===========================================================================
# build_service.approve_plan()
# ===========================================================================


class TestApprovePlan:
    """Test approve_plan function in build_service."""

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_approve_plan_success(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()

        # Register a pending plan review
        register_plan_review(str(_BUILD_ID))

        result = await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="approve")
        assert result["ok"] is True
        assert result["action"] == "approve"

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_reject_plan(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()

        register_plan_review(str(_BUILD_ID))

        result = await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="reject")
        assert result["ok"] is True
        assert result["action"] == "reject"

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_approve_plan_no_pending_review(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )

        with pytest.raises(ValueError, match="No pending plan review"):
            await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="approve")

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_approve_plan_invalid_action(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )

        with pytest.raises(ValueError, match="Invalid action"):
            await build_service.approve_plan(_PROJECT_ID, _USER_ID, action="edit")


# ===========================================================================
# build_service.commence_build()
# ===========================================================================


class TestCommenceBuild:
    """Test commence_build function in build_service."""

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_commence_build_success(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()

        register_ide_ready(str(_BUILD_ID))

        result = await build_service.commence_build(_PROJECT_ID, _USER_ID, action="commence")
        assert result["ok"] is True
        assert result["action"] == "commence"

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_cancel_before_commence(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )
        mock_build_repo.append_build_log = AsyncMock()

        register_ide_ready(str(_BUILD_ID))

        result = await build_service.commence_build(_PROJECT_ID, _USER_ID, action="cancel")
        assert result["ok"] is True
        assert result["action"] == "cancel"

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_commence_no_pending_gate(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )

        with pytest.raises(ValueError, match="No pending IDE ready gate"):
            await build_service.commence_build(_PROJECT_ID, _USER_ID)

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_commence_invalid_action(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(
            return_value={"id": _PROJECT_ID, "user_id": _USER_ID}
        )
        mock_build_repo.get_latest_build_for_project = AsyncMock(
            return_value={"id": _BUILD_ID, "user_id": _USER_ID, "status": "running"}
        )

        with pytest.raises(ValueError, match="Invalid action"):
            await build_service.commence_build(_PROJECT_ID, _USER_ID, action="restart")

    @pytest.mark.asyncio
    @patch("app.services.build_service.build_repo")
    @patch("app.services.build_service.project_repo")
    async def test_commence_no_project(self, mock_project_repo, mock_build_repo):
        mock_project_repo.get_project_by_id = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="Project not found"):
            await build_service.commence_build(_PROJECT_ID, _USER_ID)


# ===========================================================================
# Gate integration: event wait/unblock pattern
# ===========================================================================


class TestGateWaitUnblock:
    """Test that the asyncio.Event wait/unblock pattern works correctly
    for both gates (simulating what _run_build_plan_execute does)."""

    @pytest.mark.asyncio
    async def test_plan_review_unblocks_on_approve(self):
        event = register_plan_review(str(_BUILD_ID))

        async def _approve_after_delay():
            await asyncio.sleep(0.05)
            resolve_plan_review(str(_BUILD_ID), {"action": "approve"})

        asyncio.create_task(_approve_after_delay())
        await asyncio.wait_for(event.wait(), timeout=2.0)

        resp = pop_plan_review_response(str(_BUILD_ID))
        assert resp["action"] == "approve"

    @pytest.mark.asyncio
    async def test_ide_ready_unblocks_on_commence(self):
        event = register_ide_ready(str(_BUILD_ID))

        async def _commence_after_delay():
            await asyncio.sleep(0.05)
            resolve_ide_ready(str(_BUILD_ID), {"action": "commence"})

        asyncio.create_task(_commence_after_delay())
        await asyncio.wait_for(event.wait(), timeout=2.0)

        resp = pop_ide_ready_response(str(_BUILD_ID))
        assert resp["action"] == "commence"

    @pytest.mark.asyncio
    async def test_plan_review_timeout_if_no_response(self):
        event = register_plan_review(str(_BUILD_ID))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_ide_ready_timeout_if_no_response(self):
        event = register_ide_ready(str(_BUILD_ID))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), timeout=0.1)


# ===========================================================================
# Sub-agent activity broadcast
# ===========================================================================


class TestSubAgentActivity:
    """Test that tool calls in run_sub_agent emit subagent_activity events."""

    @pytest.fixture(autouse=True)
    def _patch_deps(self, tmp_path):
        self.working_dir = str(tmp_path)
        self.mock_broadcast = AsyncMock()
        from types import SimpleNamespace
        fake_settings = SimpleNamespace(
            LLM_BUILDER_MODEL="claude-opus-4-20250514",
            LLM_PLANNER_MODEL="claude-sonnet-4-20250514",
            LLM_QUESTIONNAIRE_MODEL="claude-sonnet-4-20250514",
            BUILD_PAUSE_TIMEOUT_MINUTES=30,
        )
        self.mock_repo = MagicMock()
        self.mock_repo.append_build_log = AsyncMock()
        self.mock_repo.record_build_cost = AsyncMock()

        patches = [
            patch("app.services.build._state.settings", fake_settings),
            patch("app.services.build._state.build_repo", self.mock_repo),
            patch("app.services.build._state.manager", MagicMock(send_to_user=AsyncMock())),
            patch("app.services.build._state._broadcast_build_event", self.mock_broadcast),
        ]
        self._patch_objs = patches
        for p in patches:
            p.start()
        yield
        for p in patches:
            p.stop()

    @pytest.mark.asyncio
    async def test_tool_call_emits_activity_broadcast(self):
        """When a non-CODER agent uses a tool, a subagent_activity event is broadcast."""
        from app.services.build.subagent import (
            SubAgentHandoff, SubAgentRole, run_sub_agent,
        )
        from app.clients.agent_client import StreamUsage, ToolCall

        handoff = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=_BUILD_ID,
            user_id=_USER_ID,
            assignment="Read the project structure",
            files=["src/main.py"],
        )

        # Simulate a tool call: read_file, then text response
        call_count = 0

        async def fake_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCall(id="tc1", name="read_file", input={"path": "src/main.py"})
            else:
                yield "Done reading."

        with patch("app.services.build.subagent.stream_agent", side_effect=fake_stream), \
             patch("app.services.build.subagent.execute_tool_async",
                   new_callable=AsyncMock, return_value="file contents here"):
            result = await run_sub_agent(handoff, self.working_dir, "sk-test")

        # Find the subagent_activity broadcast call
        activity_calls = [
            c for c in self.mock_broadcast.call_args_list
            if len(c.args) >= 3 and c.args[2] == "subagent_activity"
        ]
        assert len(activity_calls) >= 1
        payload = activity_calls[0].args[3]
        assert payload["tool"] == "read_file"
        assert payload["action"] == "read"
        assert payload["path"] == "src/main.py"
        assert payload["success"] is True

    @pytest.mark.asyncio
    async def test_search_tool_emits_query_in_activity(self):
        """search_code tool call includes the search query in the activity payload."""
        from app.services.build.subagent import (
            SubAgentHandoff, SubAgentRole, run_sub_agent,
        )
        from app.clients.agent_client import ToolCall

        handoff = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=_BUILD_ID,
            user_id=_USER_ID,
            assignment="Find database config",
            files=[],
        )

        call_count = 0

        async def fake_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield ToolCall(
                    id="tc1", name="search_code",
                    input={"pattern": "DATABASE_URL", "path": "src/"},
                )
            else:
                yield "Found config."

        with patch("app.services.build.subagent.stream_agent", side_effect=fake_stream), \
             patch("app.services.build.subagent.execute_tool_async",
                   new_callable=AsyncMock, return_value="2 matches found"):
            result = await run_sub_agent(handoff, self.working_dir, "sk-test")

        activity_calls = [
            c for c in self.mock_broadcast.call_args_list
            if len(c.args) >= 3 and c.args[2] == "subagent_activity"
        ]
        assert len(activity_calls) >= 1
        payload = activity_calls[0].args[3]
        assert payload["action"] == "search"
        assert payload["query"] == "DATABASE_URL"


# ===========================================================================
# Router endpoint tests
# ===========================================================================


class TestRouterEndpoints:
    """Test the approve-plan and commence endpoints via TestClient."""

    @pytest.fixture(autouse=True)
    def _setup_client(self):
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    @patch("app.services.build_service.approve_plan", new_callable=AsyncMock)
    @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    def test_approve_plan_endpoint(self, mock_get_user, mock_approve):
        from app.auth import create_token
        mock_get_user.return_value = {"id": str(_USER_ID), "github_login": "test"}
        mock_approve.return_value = {"ok": True, "build_id": str(_BUILD_ID), "action": "approve"}

        token = create_token(str(_USER_ID), "test")
        resp = self.client.post(
            f"/projects/{_PROJECT_ID}/build/approve-plan",
            json={"action": "approve"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("app.services.build_service.commence_build", new_callable=AsyncMock)
    @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    def test_commence_endpoint(self, mock_get_user, mock_commence):
        from app.auth import create_token
        mock_get_user.return_value = {"id": str(_USER_ID), "github_login": "test"}
        mock_commence.return_value = {"ok": True, "build_id": str(_BUILD_ID), "action": "commence"}

        token = create_token(str(_USER_ID), "test")
        resp = self.client.post(
            f"/projects/{_PROJECT_ID}/build/commence",
            json={"action": "commence"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("app.services.build_service.approve_plan", new_callable=AsyncMock)
    @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    def test_reject_plan_endpoint(self, mock_get_user, mock_approve):
        from app.auth import create_token
        mock_get_user.return_value = {"id": str(_USER_ID), "github_login": "test"}
        mock_approve.return_value = {"ok": True, "build_id": str(_BUILD_ID), "action": "reject"}

        token = create_token(str(_USER_ID), "test")
        resp = self.client.post(
            f"/projects/{_PROJECT_ID}/build/approve-plan",
            json={"action": "reject"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "reject"

    @patch("app.services.build_service.commence_build", new_callable=AsyncMock)
    @patch("app.api.deps.get_user_by_id", new_callable=AsyncMock)
    def test_cancel_commence_endpoint(self, mock_get_user, mock_commence):
        from app.auth import create_token
        mock_get_user.return_value = {"id": str(_USER_ID), "github_login": "test"}
        mock_commence.return_value = {"ok": True, "build_id": str(_BUILD_ID), "action": "cancel"}

        token = create_token(str(_USER_ID), "test")
        resp = self.client.post(
            f"/projects/{_PROJECT_ID}/build/commence",
            json={"action": "cancel"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["action"] == "cancel"
