"""Tests for the MCP registry bridge and unified build launcher.

Tests both modules:
- ``forge_ide.mcp.registry_bridge`` — Forge/MCP tools → Registry
- ``app.services.build.ide_launcher`` — Unified build agent launcher
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from forge_ide.contracts import ToolResponse
from forge_ide.registry import Registry


# ═══════════════════════════════════════════════════════════════════════════
# registry_bridge — Pydantic models
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistryBridgeModels:
    """Test Pydantic request models for Forge/MCP tools."""

    def test_forge_get_contract_request(self):
        from forge_ide.mcp.registry_bridge import ForgeGetContractRequest

        req = ForgeGetContractRequest(name="blueprint")
        assert req.name == "blueprint"

    def test_forge_get_contract_requires_name(self):
        from forge_ide.mcp.registry_bridge import ForgeGetContractRequest

        with pytest.raises(Exception):
            ForgeGetContractRequest()

    def test_forge_get_phase_window_request(self):
        from forge_ide.mcp.registry_bridge import ForgeGetPhaseWindowRequest

        req = ForgeGetPhaseWindowRequest(phase_number=2)
        assert req.phase_number == 2

    def test_forge_scratchpad_request_full(self):
        from forge_ide.mcp.registry_bridge import ForgeScratchpadRequest

        req = ForgeScratchpadRequest(operation="write", key="notes", value="hello")
        assert req.operation == "write"
        assert req.key == "notes"
        assert req.value == "hello"

    def test_forge_scratchpad_request_minimal(self):
        from forge_ide.mcp.registry_bridge import ForgeScratchpadRequest

        req = ForgeScratchpadRequest(operation="list")
        assert req.operation == "list"
        assert req.key is None
        assert req.value is None

    def test_forge_set_session_request(self):
        from forge_ide.mcp.registry_bridge import ForgeSetSessionRequest

        req = ForgeSetSessionRequest(
            project_id="proj-1", build_id="b-1", user_id="u-1"
        )
        assert req.project_id == "proj-1"
        assert req.build_id == "b-1"

    def test_forge_set_session_minimal(self):
        from forge_ide.mcp.registry_bridge import ForgeSetSessionRequest

        req = ForgeSetSessionRequest(project_id="proj-1")
        assert req.build_id is None
        assert req.user_id is None

    def test_forge_store_artifact_request(self):
        from forge_ide.mcp.registry_bridge import ForgeStoreArtifactRequest

        req = ForgeStoreArtifactRequest(
            project_id="p1",
            artifact_type="contract",
            key="manifesto",
            content="# Manifesto",
            ttl_hours=48,
            persist=False,
        )
        assert req.ttl_hours == 48
        assert req.persist is False

    def test_forge_store_artifact_defaults(self):
        from forge_ide.mcp.registry_bridge import ForgeStoreArtifactRequest

        req = ForgeStoreArtifactRequest(
            project_id="p1",
            artifact_type="contract",
            key="manifesto",
            content="# Manifesto",
        )
        assert req.ttl_hours == 24
        assert req.persist is True

    def test_empty_request_models(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetSummaryRequest,
            ForgeListContractsRequest,
            ForgeClearSessionRequest,
        )

        # These should instantiate with no params
        ForgeGetSummaryRequest()
        ForgeListContractsRequest()
        ForgeClearSessionRequest()

    def test_forge_get_project_contract_request(self):
        from forge_ide.mcp.registry_bridge import ForgeGetProjectContractRequest

        req = ForgeGetProjectContractRequest(contract_type="manifesto")
        assert req.contract_type == "manifesto"
        assert req.project_id is None


# ═══════════════════════════════════════════════════════════════════════════
# registry_bridge — Workspace tool adapters
# ═══════════════════════════════════════════════════════════════════════════


class TestForgeWorkspaceAdapters:
    """Test adapter functions that wrap tool_executor handlers."""

    @patch("app.services.tool_executor._exec_forge_get_contract")
    def test_adapt_forge_get_contract_ok(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetContractRequest,
            _adapt_forge_get_contract,
        )

        mock_exec.return_value = "# Blueprint\nProject overview..."
        req = ForgeGetContractRequest(name="blueprint")
        result = _adapt_forge_get_contract(req, "/tmp/workspace")

        assert result.success
        assert result.data["name"] == "blueprint"
        assert "Blueprint" in result.data["content"]
        mock_exec.assert_called_once_with({"name": "blueprint"}, "/tmp/workspace")

    @patch("app.services.tool_executor._exec_forge_get_contract")
    def test_adapt_forge_get_contract_error(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetContractRequest,
            _adapt_forge_get_contract,
        )

        mock_exec.return_value = "Error: Contract 'missing' not found"
        req = ForgeGetContractRequest(name="missing")
        result = _adapt_forge_get_contract(req, "/tmp/workspace")

        assert not result.success
        assert "not found" in result.error

    @patch("app.services.tool_executor._exec_forge_get_phase_window")
    def test_adapt_forge_get_phase_window_ok(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetPhaseWindowRequest,
            _adapt_forge_get_phase_window,
        )

        mock_exec.return_value = "Phase 0: Backend Scaffold\n- Auth module..."
        req = ForgeGetPhaseWindowRequest(phase_number=0)
        result = _adapt_forge_get_phase_window(req, "/tmp/workspace")

        assert result.success
        assert result.data["phase_number"] == 0
        assert "Backend Scaffold" in result.data["content"]

    @patch("app.services.tool_executor._exec_forge_list_contracts")
    def test_adapt_forge_list_contracts_ok(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeListContractsRequest,
            _adapt_forge_list_contracts,
        )

        mock_exec.return_value = "blueprint.md (2.3KB)\nstack.json (1.1KB)"
        req = ForgeListContractsRequest()
        result = _adapt_forge_list_contracts(req, "/tmp/workspace")

        assert result.success
        assert "blueprint.md" in result.data["contracts"]

    @patch("app.services.tool_executor._exec_forge_get_summary")
    def test_adapt_forge_get_summary_ok(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetSummaryRequest,
            _adapt_forge_get_summary,
        )

        mock_exec.return_value = "Forge Governance Framework Overview..."
        req = ForgeGetSummaryRequest()
        result = _adapt_forge_get_summary(req, "/tmp/workspace")

        assert result.success
        assert "Forge" in result.data["summary"]

    @patch("app.services.tool_executor._exec_forge_scratchpad")
    def test_adapt_forge_scratchpad_write(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeScratchpadRequest,
            _adapt_forge_scratchpad,
        )

        mock_exec.return_value = "OK: Wrote key 'notes'"
        req = ForgeScratchpadRequest(operation="write", key="notes", value="decision: use JWT")
        result = _adapt_forge_scratchpad(req, "/tmp/workspace")

        assert result.success
        mock_exec.assert_called_once_with(
            {"operation": "write", "key": "notes", "value": "decision: use JWT"},
            "/tmp/workspace",
        )

    @patch("app.services.tool_executor._exec_forge_scratchpad")
    def test_adapt_forge_scratchpad_list_omits_none(self, mock_exec):
        from forge_ide.mcp.registry_bridge import (
            ForgeScratchpadRequest,
            _adapt_forge_scratchpad,
        )

        mock_exec.return_value = "Keys: notes, decisions"
        req = ForgeScratchpadRequest(operation="list")
        result = _adapt_forge_scratchpad(req, "/tmp/workspace")

        assert result.success
        # Should NOT pass key or value when they're None
        mock_exec.assert_called_once_with({"operation": "list"}, "/tmp/workspace")


# ═══════════════════════════════════════════════════════════════════════════
# registry_bridge — MCP tool adapters
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPProjectAdapters:
    """Test adapter functions for MCP project tools (async)."""

    @pytest.mark.asyncio
    async def test_adapt_forge_set_session(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeSetSessionRequest,
            _adapt_forge_set_session,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"status": "ok", "project_id": "p1"}

            req = ForgeSetSessionRequest(project_id="p1", build_id="b1")
            result = await _adapt_forge_set_session(req, "/tmp")

            assert result.success
            mock_dispatch.assert_called_once_with(
                "forge_set_session",
                {"project_id": "p1", "build_id": "b1"},
            )

    @pytest.mark.asyncio
    async def test_adapt_forge_get_project_contract(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetProjectContractRequest,
            _adapt_forge_get_project_contract,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {
                "contract_type": "manifesto",
                "content": "# Project Manifesto",
            }

            req = ForgeGetProjectContractRequest(contract_type="manifesto")
            result = await _adapt_forge_get_project_contract(req, "/tmp")

            assert result.success
            assert result.data["contract_type"] == "manifesto"

    @pytest.mark.asyncio
    async def test_adapt_mcp_error(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetProjectContractRequest,
            _adapt_forge_get_project_contract,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"error": "Contract not found"}

            req = ForgeGetProjectContractRequest(contract_type="missing")
            result = await _adapt_forge_get_project_contract(req, "/tmp")

            assert not result.success
            assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_adapt_mcp_exception(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetBuildContractsRequest,
            _adapt_forge_get_build_contracts,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.side_effect = RuntimeError("API unreachable")

            req = ForgeGetBuildContractsRequest(build_id="b-1")
            result = await _adapt_forge_get_build_contracts(req, "/tmp")

            assert not result.success
            assert "API unreachable" in result.error


class TestMCPArtifactAdapters:
    """Test adapter functions for MCP artifact tools (async)."""

    @pytest.mark.asyncio
    async def test_adapt_forge_store_artifact(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeStoreArtifactRequest,
            _adapt_forge_store_artifact,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"stored": True, "key": "manifesto"}

            req = ForgeStoreArtifactRequest(
                project_id="p1",
                artifact_type="contract",
                key="manifesto",
                content="# Content",
            )
            result = await _adapt_forge_store_artifact(req, "/tmp")

            assert result.success
            mock_dispatch.assert_called_once_with("forge_store_artifact", {
                "project_id": "p1",
                "artifact_type": "contract",
                "key": "manifesto",
                "content": "# Content",
                "ttl_hours": 24,
                "persist": True,
            })

    @pytest.mark.asyncio
    async def test_adapt_forge_get_artifact(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeGetArtifactRequest,
            _adapt_forge_get_artifact,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"content": "stored data", "source": "memory"}

            req = ForgeGetArtifactRequest(
                project_id="p1", artifact_type="phase", key="plan_0"
            )
            result = await _adapt_forge_get_artifact(req, "/tmp")

            assert result.success
            assert result.data["content"] == "stored data"

    @pytest.mark.asyncio
    async def test_adapt_forge_list_artifacts(self):
        from forge_ide.mcp.registry_bridge import (
            ForgeListArtifactsRequest,
            _adapt_forge_list_artifacts,
        )

        with patch("forge_ide.mcp.tools.dispatch", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {
                "artifacts": [
                    {"type": "contract", "key": "manifesto"},
                    {"type": "phase", "key": "plan_0"},
                ]
            }

            req = ForgeListArtifactsRequest(project_id="p1")
            result = await _adapt_forge_list_artifacts(req, "/tmp")

            assert result.success
            assert len(result.data["artifacts"]) == 2


# ═══════════════════════════════════════════════════════════════════════════
# registry_bridge — Registration
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistration:
    """Test tool registration into Registry."""

    def test_register_forge_workspace_tools(self):
        from forge_ide.mcp.registry_bridge import register_forge_workspace_tools

        registry = Registry()
        register_forge_workspace_tools(registry)

        names = registry.tool_names()
        assert "forge_get_contract" in names
        assert "forge_get_phase_window" in names
        assert "forge_list_contracts" in names
        assert "forge_get_summary" in names
        assert "forge_scratchpad" in names
        assert len(names) == 5

    def test_register_mcp_project_tools(self):
        from forge_ide.mcp.registry_bridge import register_mcp_project_tools

        registry = Registry()
        register_mcp_project_tools(registry)

        names = registry.tool_names()
        assert "forge_set_session" in names
        assert "forge_clear_session" in names
        assert "forge_get_project_context" in names
        assert "forge_list_project_contracts" in names
        assert "forge_get_project_contract" in names
        assert "forge_get_build_contracts" in names
        assert len(names) == 6

    def test_register_mcp_artifact_tools(self):
        from forge_ide.mcp.registry_bridge import register_mcp_artifact_tools

        registry = Registry()
        register_mcp_artifact_tools(registry)

        names = registry.tool_names()
        assert "forge_store_artifact" in names
        assert "forge_get_artifact" in names
        assert "forge_list_artifacts" in names
        assert "forge_clear_artifacts" in names
        assert len(names) == 4

    def test_register_forge_tools_all(self):
        from forge_ide.mcp.registry_bridge import register_forge_tools

        registry = Registry()
        register_forge_tools(registry)

        names = registry.tool_names()
        assert len(names) == 15  # 5 workspace + 6 project + 4 artifact

    def test_no_duplicate_names(self):
        from forge_ide.mcp.registry_bridge import register_forge_tools

        registry = Registry()
        register_forge_tools(registry)

        names = registry.tool_names()
        assert len(names) == len(set(names)), "Duplicate tool names detected"

    def test_tool_definitions_are_valid(self):
        """Every registered tool should produce a valid Anthropic tool definition."""
        from forge_ide.mcp.registry_bridge import register_forge_tools

        registry = Registry()
        register_forge_tools(registry)

        defs = registry.list_tools()
        assert len(defs) == 15

        for d in defs:
            assert "name" in d
            assert "description" in d
            assert "input_schema" in d
            assert d["input_schema"]["type"] == "object"


# ═══════════════════════════════════════════════════════════════════════════
# ide_launcher — Registry factory
# ═══════════════════════════════════════════════════════════════════════════


class TestCreateBuildRegistry:
    """Test the create_build_registry factory."""

    def test_full_registry(self):
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry()
        names = registry.tool_names()

        # 7 core + apply_patch + 5 forge workspace + 6 mcp project + 4 mcp artifact = 23
        assert len(names) == 23
        assert "read_file" in names
        assert "write_file" in names
        assert "apply_patch" in names
        assert "forge_get_contract" in names
        assert "forge_set_session" in names
        assert "forge_store_artifact" in names

    def test_readonly_registry(self):
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry(include_write_tools=False)
        names = registry.tool_names()

        assert "read_file" in names
        assert "search_code" in names
        assert "check_syntax" in names
        assert "write_file" not in names
        assert "apply_patch" not in names

    def test_no_mcp_tools(self):
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry(
            include_mcp_project=False,
            include_mcp_artifacts=False,
        )
        names = registry.tool_names()

        assert "forge_get_contract" in names       # workspace tool
        assert "forge_set_session" not in names     # MCP project
        assert "forge_store_artifact" not in names  # MCP artifact

    def test_no_forge_workspace(self):
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry(include_forge_workspace=False)
        names = registry.tool_names()

        assert "forge_get_contract" not in names
        assert "forge_scratchpad" not in names
        assert "read_file" in names  # core IDE still present


class TestRolePresets:
    """Test convenience registry presets for sub-agent roles."""

    def test_scout_registry_is_readonly(self):
        from app.services.build.ide_launcher import create_scout_registry

        registry = create_scout_registry()
        names = registry.tool_names()

        assert "read_file" in names
        assert "search_code" in names
        assert "write_file" not in names
        assert "apply_patch" not in names
        assert "forge_get_contract" in names

    def test_coder_registry_has_write(self):
        from app.services.build.ide_launcher import create_coder_registry

        registry = create_coder_registry()
        names = registry.tool_names()

        assert "write_file" in names
        assert "apply_patch" in names
        assert "forge_get_contract" in names

    def test_auditor_registry_is_readonly(self):
        from app.services.build.ide_launcher import create_auditor_registry

        registry = create_auditor_registry()
        names = registry.tool_names()

        assert "write_file" not in names
        assert "forge_store_artifact" not in names  # no artifact tools
        assert "forge_get_contract" in names


# ═══════════════════════════════════════════════════════════════════════════
# ide_launcher — MCP session setup
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPSessionSetup:
    """Test MCP session configuration."""

    @patch("forge_ide.mcp.session.set_session")
    def test_setup_with_all_ids(self, mock_set):
        from app.services.build.ide_launcher import _setup_mcp_session

        _setup_mcp_session("proj-1", "build-1", "user-1")
        mock_set.assert_called_once_with(
            project_id="proj-1", build_id="build-1", user_id="user-1"
        )

    @patch("forge_ide.mcp.session.set_session")
    def test_setup_with_none_project_skips(self, mock_set):
        from app.services.build.ide_launcher import _setup_mcp_session

        _setup_mcp_session(None, None, None)
        mock_set.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# ide_launcher — Event tracking
# ═══════════════════════════════════════════════════════════════════════════


class TestEventHandler:
    """Test the composite event handler and build event tracker."""

    def _make_tool_result_event(
        self, tool_name: str, success: bool, data: dict | None = None
    ):
        from forge_ide.agent import ToolResultEvent

        resp = ToolResponse.ok(data or {}) if success else ToolResponse.fail("error")
        return ToolResultEvent(
            turn=1, elapsed_ms=100,
            tool_name=tool_name,
            tool_use_id="tid-1",
            response=resp,
        )

    @pytest.mark.asyncio
    async def test_tracks_write_file(self):
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = self._make_tool_result_event(
            "write_file", True, {"path": "src/main.py", "bytes_written": 100}
        )
        await handler(event)

        assert "src/main.py" in tracker.files_written

    @pytest.mark.asyncio
    async def test_tracks_apply_patch(self):
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = self._make_tool_result_event(
            "apply_patch", True, {"path": "src/utils.py", "hunks_applied": 1}
        )
        await handler(event)

        assert "src/utils.py" in tracker.files_written

    @pytest.mark.asyncio
    async def test_no_duplicate_tracking(self):
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = self._make_tool_result_event(
            "write_file", True, {"path": "src/main.py", "bytes_written": 100}
        )
        await handler(event)
        await handler(event)

        assert len(tracker.files_written) == 1

    @pytest.mark.asyncio
    async def test_failed_write_not_tracked(self):
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = self._make_tool_result_event("write_file", False)
        await handler(event)

        assert len(tracker.files_written) == 0

    @pytest.mark.asyncio
    async def test_phase_signoff_detection(self):
        from forge_ide.agent import TextEvent
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = TextEvent(
            turn=5, elapsed_ms=5000,
            text="=== PHASE SIGN-OFF: PASS ===\nPhase: Backend\n=== END PHASE SIGN-OFF ===",
        )
        await handler(event)

        assert len(tracker.phase_signoffs) == 1

    @pytest.mark.asyncio
    async def test_error_tracking(self):
        from forge_ide.agent import ErrorEvent
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        tracker = _BuildEventTracker()
        handler = _make_composite_handler(tracker=tracker)

        event = ErrorEvent(turn=3, elapsed_ms=3000, error="LLM API timeout")
        await handler(event)

        assert tracker.last_error == "LLM API timeout"

    @pytest.mark.asyncio
    async def test_ws_handler_called(self):
        from forge_ide.agent import TextEvent
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        ws_mock = AsyncMock()
        tracker = _BuildEventTracker()
        handler = _make_composite_handler(
            ws_handler=ws_mock, tracker=tracker
        )

        event = TextEvent(turn=1, elapsed_ms=100, text="Working...")
        await handler(event)

        ws_mock.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_extra_handler_called(self):
        from forge_ide.agent import TextEvent
        from app.services.build.ide_launcher import (
            _BuildEventTracker,
            _make_composite_handler,
        )

        extra_mock = AsyncMock()
        tracker = _BuildEventTracker()
        handler = _make_composite_handler(
            tracker=tracker, extra_handler=extra_mock
        )

        event = TextEvent(turn=1, elapsed_ms=100, text="Working...")
        await handler(event)

        extra_mock.assert_called_once_with(event)


# ═══════════════════════════════════════════════════════════════════════════
# ide_launcher — launch_build_agent
# ═══════════════════════════════════════════════════════════════════════════


class TestLaunchBuildAgent:
    """Test the unified launch_build_agent function."""

    @pytest.mark.asyncio
    async def test_launch_success(self):
        from forge_ide.agent import DoneEvent
        from app.services.build.ide_launcher import launch_build_agent

        done = DoneEvent(
            turn=5, elapsed_ms=10000,
            final_text="Phase complete",
            total_input_tokens=5000,
            total_output_tokens=3000,
            tool_calls_made=12,
        )

        with patch("app.services.build.ide_launcher.run_agent", new_callable=AsyncMock) as mock_run, \
             patch("app.services.build.ide_launcher._setup_mcp_session") as mock_session:
            mock_run.return_value = done

            result = await launch_build_agent(
                "Build Phase 0",
                api_key="sk-test",
                working_dir="/tmp/workspace",
                model="claude-opus-4-20250514",
                system_prompt="You are a builder",
                max_turns=30,
                project_id="proj-1",
                build_id="build-1",
                user_id="user-1",
                broadcast_ws=False,
            )

            assert result.final_text == "Phase complete"
            assert result.total_input_tokens == 5000
            assert result.total_output_tokens == 3000
            assert result.tool_calls_made == 12
            assert result.turns == 5
            assert result.elapsed_ms == 10000
            assert result.error is None

            mock_session.assert_called_once_with("proj-1", "build-1", "user-1")
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_launch_with_error(self):
        from app.services.build.ide_launcher import launch_build_agent

        with patch("app.services.build.ide_launcher.run_agent", new_callable=AsyncMock) as mock_run, \
             patch("app.services.build.ide_launcher._setup_mcp_session"):
            mock_run.side_effect = RuntimeError("API key expired")

            result = await launch_build_agent(
                "Build Phase 0",
                api_key="sk-bad",
                working_dir="/tmp/workspace",
                broadcast_ws=False,
            )

            assert result.error == "API key expired"
            assert result.final_text == ""

    @pytest.mark.asyncio
    async def test_launch_uses_provided_registry(self):
        from forge_ide.agent import DoneEvent
        from app.services.build.ide_launcher import launch_build_agent

        custom_registry = Registry()
        # Register a minimal tool
        from forge_ide.adapters import _adapt_read_file
        from forge_ide.contracts import ReadFileRequest
        custom_registry.register("read_file", _adapt_read_file, ReadFileRequest, "Read")

        done = DoneEvent(
            turn=1, elapsed_ms=100,
            final_text="Done",
            total_input_tokens=100,
            total_output_tokens=50,
            tool_calls_made=0,
        )

        with patch("app.services.build.ide_launcher.run_agent", new_callable=AsyncMock) as mock_run, \
             patch("app.services.build.ide_launcher._setup_mcp_session"):
            mock_run.return_value = done

            result = await launch_build_agent(
                "Test task",
                api_key="sk-test",
                working_dir="/tmp",
                registry=custom_registry,
                broadcast_ws=False,
            )

            # Verify the custom registry was passed through
            call_args = mock_run.call_args
            config = call_args.kwargs.get("config") or call_args[1].get("config")
            # The registry is the positional arg
            assert call_args[1]["registry"] is custom_registry or call_args.args[1] is custom_registry

    @pytest.mark.asyncio
    async def test_launch_creates_registry_when_none(self):
        from forge_ide.agent import DoneEvent
        from app.services.build.ide_launcher import launch_build_agent

        done = DoneEvent(
            turn=1, elapsed_ms=100,
            final_text="Done",
            total_input_tokens=100,
            total_output_tokens=50,
            tool_calls_made=0,
        )

        with patch("app.services.build.ide_launcher.run_agent", new_callable=AsyncMock) as mock_run, \
             patch("app.services.build.ide_launcher._setup_mcp_session"):
            mock_run.return_value = done

            result = await launch_build_agent(
                "Test task",
                api_key="sk-test",
                working_dir="/tmp",
                broadcast_ws=False,
            )

            # Should have created a full registry
            call_args = mock_run.call_args
            # registry is the 2nd positional arg
            registry = call_args.args[1] if len(call_args.args) > 1 else call_args.kwargs.get("registry")
            assert registry is not None
            assert len(registry.tool_names()) == 23  # full surface

    @pytest.mark.asyncio
    async def test_launch_config_passthrough(self):
        """Verify all config params are passed through to AgentConfig."""
        from forge_ide.agent import DoneEvent, AgentConfig
        from app.services.build.ide_launcher import launch_build_agent

        done = DoneEvent(
            turn=1, elapsed_ms=100,
            final_text="Done",
            total_input_tokens=100,
            total_output_tokens=50,
            tool_calls_made=0,
        )

        with patch("app.services.build.ide_launcher.run_agent", new_callable=AsyncMock) as mock_run, \
             patch("app.services.build.ide_launcher._setup_mcp_session"):
            mock_run.return_value = done

            await launch_build_agent(
                "Build it",
                api_key="sk-123",
                working_dir="/work",
                model="claude-opus-4-20250514",
                system_prompt="Be awesome",
                max_turns=25,
                max_tokens=8192,
                context_window_limit=100_000,
                compaction_target=80_000,
                broadcast_ws=False,
            )

            call_kwargs = mock_run.call_args.kwargs
            config: AgentConfig = call_kwargs["config"]
            assert config.api_key == "sk-123"
            assert config.model == "claude-opus-4-20250514"
            assert config.system_prompt == "Be awesome"
            assert config.max_turns == 25
            assert config.max_tokens == 8192
            assert config.working_dir == "/work"
            assert config.context_window_limit == 100_000
            assert config.compaction_target == 80_000


# ═══════════════════════════════════════════════════════════════════════════
# ide_launcher — BuildAgentResult
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildAgentResult:
    """Test the BuildAgentResult dataclass."""

    def test_result_frozen(self):
        from app.services.build.ide_launcher import BuildAgentResult

        result = BuildAgentResult(
            final_text="done",
            total_input_tokens=100,
            total_output_tokens=50,
            tool_calls_made=5,
            turns=3,
            elapsed_ms=1000,
        )
        with pytest.raises(AttributeError):
            result.final_text = "changed"

    def test_result_defaults(self):
        from app.services.build.ide_launcher import BuildAgentResult

        result = BuildAgentResult(
            final_text="",
            total_input_tokens=0,
            total_output_tokens=0,
            tool_calls_made=0,
            turns=0,
            elapsed_ms=0,
        )
        assert result.files_written == ()
        assert result.error is None

    def test_result_with_files(self):
        from app.services.build.ide_launcher import BuildAgentResult

        result = BuildAgentResult(
            final_text="Phase 0 done",
            total_input_tokens=5000,
            total_output_tokens=3000,
            tool_calls_made=15,
            turns=8,
            elapsed_ms=30000,
            files_written=("src/main.py", "src/config.py", "tests/test_main.py"),
        )
        assert len(result.files_written) == 3
        assert "src/main.py" in result.files_written


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Registry dispatch with Forge workspace tools
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistryDispatchIntegration:
    """Test that Forge tools work correctly through Registry dispatch."""

    @pytest.mark.asyncio
    @patch("app.services.tool_executor._exec_forge_get_contract")
    async def test_dispatch_forge_get_contract(self, mock_exec):
        from forge_ide.mcp.registry_bridge import register_forge_workspace_tools

        mock_exec.return_value = "# Blueprint content"

        registry = Registry()
        register_forge_workspace_tools(registry)

        result = await registry.dispatch(
            "forge_get_contract",
            {"name": "blueprint"},
            "/tmp/workspace",
        )

        assert result.success
        assert result.data["name"] == "blueprint"
        assert "Blueprint content" in result.data["content"]

    @pytest.mark.asyncio
    @patch("app.services.tool_executor._exec_forge_scratchpad")
    async def test_dispatch_forge_scratchpad(self, mock_exec):
        from forge_ide.mcp.registry_bridge import register_forge_workspace_tools

        mock_exec.return_value = "OK: Wrote key 'arch'"

        registry = Registry()
        register_forge_workspace_tools(registry)

        result = await registry.dispatch(
            "forge_scratchpad",
            {"operation": "write", "key": "arch", "value": "layered"},
            "/tmp/workspace",
        )

        assert result.success

    @pytest.mark.asyncio
    async def test_dispatch_validates_params(self):
        from forge_ide.mcp.registry_bridge import register_forge_workspace_tools

        registry = Registry()
        register_forge_workspace_tools(registry)

        # forge_get_contract requires 'name' — passing empty should fail validation
        result = await registry.dispatch(
            "forge_get_contract",
            {},  # missing required 'name'
            "/tmp/workspace",
        )

        assert not result.success
        assert "Invalid params" in result.error


# ═══════════════════════════════════════════════════════════════════════════
# Integration: Both flows use the same Registry
# ═══════════════════════════════════════════════════════════════════════════


class TestUnifiedToolSurface:
    """Verify that the unified registry has ALL tools needed by both
    the conversation build and the plan-execute build."""

    def test_conversation_build_tools_present(self):
        """Conversation build needs: 8 core + forge workspace tools."""
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry()
        names = set(registry.tool_names())

        # Core IDE tools
        for tool in [
            "read_file", "list_directory", "search_code",
            "write_file", "run_tests", "check_syntax", "run_command",
            "apply_patch",
        ]:
            assert tool in names, f"Missing core tool: {tool}"

        # Forge workspace tools
        for tool in [
            "forge_get_contract", "forge_get_phase_window",
            "forge_list_contracts", "forge_get_summary", "forge_scratchpad",
        ]:
            assert tool in names, f"Missing forge workspace tool: {tool}"

    def test_plan_execute_tools_present(self):
        """Plan-execute build needs: core + forge + MCP artifact tools."""
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry()
        names = set(registry.tool_names())

        # MCP artifact tools (for plan_artifacts module)
        for tool in [
            "forge_store_artifact", "forge_get_artifact",
            "forge_list_artifacts", "forge_clear_artifacts",
        ]:
            assert tool in names, f"Missing MCP artifact tool: {tool}"

        # MCP project tools (for DB-stored contracts)
        for tool in [
            "forge_get_project_context", "forge_get_project_contract",
            "forge_get_build_contracts",
        ]:
            assert tool in names, f"Missing MCP project tool: {tool}"

    def test_total_tool_count(self):
        """Full registry should have exactly 23 tools."""
        from app.services.build.ide_launcher import create_build_registry

        registry = create_build_registry()
        assert len(registry.tool_names()) == 23
