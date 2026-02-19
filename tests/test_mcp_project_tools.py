"""Tests for Phase E: MCP project-scoped tools — session, project handlers, dispatch.

Covers:
- forge_ide.mcp.session (set/get/clear/resolve)
- forge_ide.mcp.project (project handlers with mocked API)
- forge_ide.mcp.tools dispatch routing for _PROJECT_TOOLS
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from forge_ide.mcp.session import (
    MCPSession,
    clear_session,
    get_session,
    resolve_build_id,
    resolve_project_id,
    set_session,
    _session,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_session():
    """Reset the module-level session singleton."""
    _session.clear()


# ═══════════════════════════════════════════════════════════════════════════
# MCPSession dataclass
# ═══════════════════════════════════════════════════════════════════════════


class TestMCPSession:
    def test_default_state(self):
        s = MCPSession()
        assert s.project_id is None
        assert s.build_id is None
        assert s.user_id is None

    def test_as_dict(self):
        s = MCPSession(project_id="p1", build_id="b1", user_id="u1")
        d = s.as_dict()
        assert d == {"project_id": "p1", "build_id": "b1", "user_id": "u1"}

    def test_clear(self):
        s = MCPSession(project_id="p1", build_id="b1")
        s.clear()
        assert s.project_id is None
        assert s.build_id is None
        assert s.user_id is None


# ═══════════════════════════════════════════════════════════════════════════
# set_session / get_session / clear_session
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionFunctions:
    def setup_method(self):
        _reset_session()

    def test_set_session_returns_ok(self):
        result = set_session("proj-1", build_id="build-7", user_id="user-A")
        assert result["ok"] is True
        assert result["project_id"] == "proj-1"
        assert result["build_id"] == "build-7"
        assert result["user_id"] == "user-A"

    def test_set_session_minimal(self):
        result = set_session("proj-2")
        assert result["ok"] is True
        assert result["project_id"] == "proj-2"
        assert result["build_id"] is None

    def test_get_session_reflects_set(self):
        set_session("proj-3", build_id="b3")
        s = get_session()
        assert s.project_id == "proj-3"
        assert s.build_id == "b3"

    def test_clear_session(self):
        set_session("proj-4")
        result = clear_session()
        assert result["ok"] is True
        assert result["cleared"] is True
        s = get_session()
        assert s.project_id is None

    def test_set_overwrites_previous(self):
        set_session("proj-A")
        set_session("proj-B", build_id="b-B")
        s = get_session()
        assert s.project_id == "proj-B"
        assert s.build_id == "b-B"


# ═══════════════════════════════════════════════════════════════════════════
# resolve_project_id / resolve_build_id
# ═══════════════════════════════════════════════════════════════════════════


class TestResolvers:
    def setup_method(self):
        _reset_session()

    def test_resolve_project_id_from_args(self):
        set_session("session-proj")
        assert resolve_project_id({"project_id": "arg-proj"}) == "arg-proj"

    def test_resolve_project_id_from_session(self):
        set_session("session-proj")
        assert resolve_project_id({}) == "session-proj"

    def test_resolve_project_id_none_when_both_empty(self):
        assert resolve_project_id({}) is None

    def test_resolve_build_id_from_args(self):
        set_session("p", build_id="session-build")
        assert resolve_build_id({"build_id": "arg-build"}) == "arg-build"

    def test_resolve_build_id_from_session(self):
        set_session("p", build_id="session-build")
        assert resolve_build_id({}) == "session-build"

    def test_resolve_build_id_none_when_both_empty(self):
        assert resolve_build_id({}) is None


# ═══════════════════════════════════════════════════════════════════════════
# Project tool handlers (mocked API)
# ═══════════════════════════════════════════════════════════════════════════


class TestProjectHandlers:
    """Test forge_ide.mcp.project handlers with mocked api_get."""

    def setup_method(self):
        _reset_session()

    def test_get_project_context_success(self):
        mock_resp = {
            "project": {"id": "p1", "name": "Test", "status": "ready"},
            "contracts": [{"contract_type": "manifesto", "version": 1}],
            "latest_batch": 3,
            "build_count": 2,
        }
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.project import get_project_context
            result = asyncio.run(get_project_context({"project_id": "p1"}))
            assert result["project"]["id"] == "p1"
            assert len(result["contracts"]) == 1

    def test_get_project_context_uses_session(self):
        set_session("sess-proj")
        mock_resp = {"project": {"id": "sess-proj"}, "contracts": [], "latest_batch": None, "build_count": 0}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp) as mock:
            from forge_ide.mcp.project import get_project_context
            asyncio.run(get_project_context({}))
            mock.assert_called_once_with("/api/mcp/context/sess-proj")

    def test_get_project_context_missing_pid(self):
        from forge_ide.mcp.project import get_project_context
        result = asyncio.run(get_project_context({}))
        assert "error" in result

    def test_list_project_contracts_extracts_items(self):
        mock_resp = {
            "project": {"id": "p1"},
            "contracts": [
                {"contract_type": "manifesto", "version": 2},
                {"contract_type": "stack", "version": 1},
            ],
            "latest_batch": 1,
            "build_count": 0,
        }
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.project import list_project_contracts
            result = asyncio.run(list_project_contracts({"project_id": "p1"}))
            assert result["count"] == 2
            assert result["items"][0]["contract_type"] == "manifesto"

    def test_list_project_contracts_missing_pid(self):
        from forge_ide.mcp.project import list_project_contracts
        result = asyncio.run(list_project_contracts({}))
        assert "error" in result

    def test_get_project_contract_success(self):
        mock_resp = {
            "contract_type": "stack",
            "content": "# Stack\nPython 3.12",
            "version": 3,
            "project_id": "p1",
            "source": "project_db",
        }
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.project import get_project_contract
            result = asyncio.run(get_project_contract({"project_id": "p1", "contract_type": "stack"}))
            assert result["content"] == "# Stack\nPython 3.12"
            assert result["source"] == "project_db"

    def test_get_project_contract_missing_type(self):
        from forge_ide.mcp.project import get_project_contract
        result = asyncio.run(get_project_contract({"project_id": "p1"}))
        assert "error" in result

    def test_get_project_contract_missing_pid(self):
        from forge_ide.mcp.project import get_project_contract
        result = asyncio.run(get_project_contract({"contract_type": "stack"}))
        assert "error" in result

    def test_get_build_contracts_success(self):
        mock_resp = {
            "build_id": "b1",
            "batch": 5,
            "pinned_at": "2025-01-15T10:30:00Z",
            "contracts": [
                {"contract_type": "manifesto", "content": "# M"},
                {"contract_type": "stack", "content": "# S"},
            ],
        }
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.project import get_build_contracts
            result = asyncio.run(get_build_contracts({"build_id": "b1"}))
            assert result["batch"] == 5
            assert len(result["contracts"]) == 2

    def test_get_build_contracts_uses_session(self):
        set_session("p1", build_id="sess-build")
        mock_resp = {"build_id": "sess-build", "batch": 1, "contracts": []}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp) as mock:
            from forge_ide.mcp.project import get_build_contracts
            asyncio.run(get_build_contracts({}))
            mock.assert_called_once_with("/api/mcp/build/sess-build/contracts")

    def test_get_build_contracts_missing_bid(self):
        from forge_ide.mcp.project import get_build_contracts
        result = asyncio.run(get_build_contracts({}))
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════
# Dispatch routing for _PROJECT_TOOLS
# ═══════════════════════════════════════════════════════════════════════════


class TestDispatchProjectTools:
    """Verify tools.dispatch() routes project-scoped tools correctly."""

    def setup_method(self):
        _reset_session()

    def test_dispatch_set_session(self):
        from forge_ide.mcp.tools import dispatch
        result = asyncio.run(dispatch("forge_set_session", {"project_id": "p-1", "build_id": "b-1"}))
        assert result["ok"] is True
        assert result["project_id"] == "p-1"
        assert result["build_id"] == "b-1"

    def test_dispatch_set_session_missing_pid(self):
        from forge_ide.mcp.tools import dispatch
        result = asyncio.run(dispatch("forge_set_session", {}))
        assert "error" in result

    def test_dispatch_clear_session(self):
        set_session("proj-x")
        from forge_ide.mcp.tools import dispatch
        result = asyncio.run(dispatch("forge_clear_session", {}))
        assert result["ok"] is True
        assert result["cleared"] is True
        assert get_session().project_id is None

    def test_dispatch_get_project_context(self):
        mock_resp = {"project": {"id": "p1"}, "contracts": [], "latest_batch": None, "build_count": 0}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.tools import dispatch
            result = asyncio.run(dispatch("forge_get_project_context", {"project_id": "p1"}))
            assert "project" in result

    def test_dispatch_list_project_contracts(self):
        mock_resp = {"project": {"id": "p1"}, "contracts": [{"contract_type": "stack", "version": 1}], "latest_batch": 1, "build_count": 0}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.tools import dispatch
            result = asyncio.run(dispatch("forge_list_project_contracts", {"project_id": "p1"}))
            assert result["count"] == 1

    def test_dispatch_get_project_contract(self):
        mock_resp = {"contract_type": "physics", "content": "openapi: 3.0", "version": 1, "project_id": "p1", "source": "project_db"}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.tools import dispatch
            result = asyncio.run(dispatch("forge_get_project_contract", {"project_id": "p1", "contract_type": "physics"}))
            assert result["content"] == "openapi: 3.0"

    def test_dispatch_get_build_contracts(self):
        mock_resp = {"build_id": "b1", "batch": 2, "contracts": [{"contract_type": "manifesto", "content": "#"}]}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp):
            from forge_ide.mcp.tools import dispatch
            result = asyncio.run(dispatch("forge_get_build_contracts", {"build_id": "b1"}))
            assert result["batch"] == 2

    def test_project_tools_bypass_local_mode(self):
        """Project tools must always proxy to API, even in LOCAL_MODE."""
        mock_resp = {"project": {"id": "p1"}, "contracts": [], "latest_batch": None, "build_count": 0}
        with patch("forge_ide.mcp.project.api_get", new_callable=AsyncMock, return_value=mock_resp), \
             patch("forge_ide.mcp.tools.LOCAL_MODE", True):
            from forge_ide.mcp.tools import dispatch
            result = asyncio.run(dispatch("forge_get_project_context", {"project_id": "p1"}))
            assert "project" in result  # Routed to project handler, not local


# ═══════════════════════════════════════════════════════════════════════════
# Tool definitions catalogue
# ═══════════════════════════════════════════════════════════════════════════


class TestToolDefinitions:
    def test_all_project_tools_have_definitions(self):
        from forge_ide.mcp.tools import TOOL_DEFINITIONS, _PROJECT_TOOLS
        tool_names = {t["name"] for t in TOOL_DEFINITIONS}
        for name in _PROJECT_TOOLS:
            assert name in tool_names, f"{name} missing from TOOL_DEFINITIONS"

    def test_project_tool_count(self):
        from forge_ide.mcp.tools import _PROJECT_TOOLS
        # session (set+clear) + project context + list + get + build = 6
        assert len(_PROJECT_TOOLS) == 6

    def test_tool_definitions_have_required_fields(self):
        from forge_ide.mcp.tools import TOOL_DEFINITIONS
        for tool in TOOL_DEFINITIONS:
            assert "name" in tool, f"Tool missing name: {tool}"
            assert "description" in tool, f"Tool {tool['name']} missing description"
            assert "inputSchema" in tool, f"Tool {tool['name']} missing inputSchema"

    def test_no_duplicate_tool_names(self):
        from forge_ide.mcp.tools import TOOL_DEFINITIONS
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), f"Duplicate tool names: {[n for n in names if names.count(n) > 1]}"

    def test_total_tool_count(self):
        from forge_ide.mcp.tools import TOOL_DEFINITIONS
        # 8 governance + 4 artifact + 6 project = 18
        assert len(TOOL_DEFINITIONS) == 18
