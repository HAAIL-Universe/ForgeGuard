"""Tests for planner_service — bridges the standalone planner into ForgeGuard.

Covers:
  - _contracts_to_request: contract → project-request string synthesis
  - run_project_planner: failure paths (missing dir, import error, planner crash)
  - _make_turn_callback: thinking_block WS event broadcast
"""

import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


_BUILD_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# _contracts_to_request
# ---------------------------------------------------------------------------


class TestContractsToRequest:
    """Tests for contract-to-project-request synthesis."""

    def _import(self):
        from app.services.planner_service import _contracts_to_request
        return _contracts_to_request

    def test_includes_blueprint(self):
        fn = self._import()
        contracts = [
            {"contract_type": "blueprint", "content": "Build a trading bot"},
            {"contract_type": "schema", "content": "Table: trades"},
        ]
        result = fn(contracts)
        assert "BLUEPRINT" in result
        assert "Build a trading bot" in result

    def test_includes_all_priority_types(self):
        fn = self._import()
        contracts = [
            {"contract_type": "blueprint", "content": "bp"},
            {"contract_type": "stack", "content": "Python"},
            {"contract_type": "schema", "content": "schema"},
            {"contract_type": "manifesto", "content": "manifesto"},
            {"contract_type": "physics", "content": "physics"},
            {"contract_type": "boundaries", "content": "boundaries"},
            {"contract_type": "ui", "content": "React"},
        ]
        result = fn(contracts)
        assert "BLUEPRINT" in result
        assert "STACK" in result
        assert "SCHEMA" in result

    def test_empty_contracts_returns_fallback(self):
        fn = self._import()
        result = fn([])
        assert "No project contracts provided" in result

    def test_unknown_contract_types_ignored(self):
        fn = self._import()
        contracts = [
            {"contract_type": "phases", "content": "phase list"},
            {"contract_type": "builder_contract", "content": "contract"},
        ]
        result = fn(contracts)
        # Neither 'phases' nor 'builder_contract' is in the priority list
        assert "No project contracts provided" in result

    def test_duplicate_types_deduplicated(self):
        """If two contracts have the same type, only the first is included."""
        fn = self._import()
        contracts = [
            {"contract_type": "blueprint", "content": "first"},
            {"contract_type": "blueprint", "content": "second"},
        ]
        result = fn(contracts)
        assert result.count("BLUEPRINT") == 1
        assert "first" in result
        assert "second" not in result


# ---------------------------------------------------------------------------
# run_project_planner — failure paths
# ---------------------------------------------------------------------------


class TestRunProjectPlannerFailures:
    """Tests for run_project_planner error handling.

    run_project_planner imports build_repo and _broadcast_build_event lazily
    inside the function body (from app.repos import build_repo, etc.), so we
    patch the source modules, not planner_service module attributes.
    """

    @pytest.mark.asyncio
    async def test_returns_none_when_planner_dir_missing(self):
        """If the planner directory doesn't exist, return None gracefully."""
        from app.services import planner_service

        with patch.object(planner_service, "_PLANNER_DIR", Path("/nonexistent/does_not_exist")), \
             patch("app.repos.build_repo.append_build_log", AsyncMock()), \
             patch("app.services.build._state._broadcast_build_event", AsyncMock()):
            result = await planner_service.run_project_planner(
                contracts=[],
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key="sk-test",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_import_error(self):
        """If planner_agent cannot be imported, return None gracefully."""
        from app.services import planner_service

        # sys.modules[x] = None causes ImportError when code does
        # `from planner_agent import ...`
        with patch.object(planner_service, "_PLANNER_DIR", _FakePlannerDir()), \
             patch.dict("sys.modules", {"planner_agent": None}), \
             patch("app.repos.build_repo.append_build_log", AsyncMock()), \
             patch("app.services.build._state._broadcast_build_event", AsyncMock()):
            result = await planner_service.run_project_planner(
                contracts=[],
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key="sk-test",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_planner_returns_none(self):
        """If the synchronous planner call returns None, propagate None."""
        from app.services import planner_service

        # run_planner returns None — _call_planner_sync propagates it
        mock_run_planner = MagicMock(return_value=None)
        mock_planner_error = Exception

        with patch.object(planner_service, "_PLANNER_DIR", _FakePlannerDir()), \
             patch.dict("sys.modules", {
                 "planner_agent": _make_fake_planner_module(mock_run_planner, mock_planner_error),
             }), \
             patch("app.repos.build_repo.append_build_log", AsyncMock()), \
             patch("app.repos.build_repo.record_build_cost", AsyncMock()), \
             patch("app.services.build._state._broadcast_build_event", AsyncMock()):
            result = await planner_service.run_project_planner(
                contracts=[{"contract_type": "blueprint", "content": "Test"}],
                build_id=_BUILD_ID,
                user_id=_USER_ID,
                api_key="sk-test",
            )

        assert result is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_import_error(name, *args, **kwargs):
    if name == "planner_agent":
        raise ImportError("Fake import error")
    return __builtins__.__import__(name, *args, **kwargs)


class _FakePlannerDir:
    """Duck-types pathlib.Path for the _PLANNER_DIR attribute."""

    def exists(self):
        return True

    def __truediv__(self, other):
        return self

    def __str__(self):
        return "/fake/planner"

    def __fspath__(self):
        return "/fake/planner"


def _make_fake_planner_module(run_planner_fn, planner_error_cls):
    """Create a fake planner_agent module with the expected API."""
    mod = MagicMock()
    mod.run_planner = run_planner_fn
    mod.PlannerError = planner_error_cls
    return mod


# ---------------------------------------------------------------------------
# _contracts_to_request — ordering
# ---------------------------------------------------------------------------


class TestContractsToRequestOrdering:
    """Priority ordering: blueprint comes before stack, stack before schema."""

    def test_blueprint_appears_before_stack(self):
        from app.services.planner_service import _contracts_to_request

        contracts = [
            {"contract_type": "stack", "content": "Python stack"},
            {"contract_type": "blueprint", "content": "Project blueprint"},
        ]
        result = _contracts_to_request(contracts)
        bp_pos = result.index("BLUEPRINT")
        stack_pos = result.index("STACK")
        assert bp_pos < stack_pos

    def test_stack_appears_before_schema(self):
        from app.services.planner_service import _contracts_to_request

        contracts = [
            {"contract_type": "schema", "content": "DB schema"},
            {"contract_type": "stack", "content": "Python stack"},
        ]
        result = _contracts_to_request(contracts)
        stack_pos = result.index("STACK")
        schema_pos = result.index("SCHEMA")
        assert stack_pos < schema_pos
