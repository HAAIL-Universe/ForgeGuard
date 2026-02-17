"""Tests for Phase 21 — plan-then-execute architecture.

Covers:
  21.1  _generate_file_manifest
  21.2  _generate_single_file
  21.3  _verify_phase_output
  21.5  _calculate_context_budget
  21.7  _select_contracts_for_file
  21.8  _generate_fix_manifest
  21.10 _run_build dispatcher / backward compat
"""

import asyncio
import json
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import build_service
from app.services.build_service import (
    _select_contracts_for_file,
    _calculate_context_budget,
    _topological_sort,
    _CONTRACT_RELEVANCE,
)

# ---------------------------------------------------------------------------
# Test data fixtures
# ---------------------------------------------------------------------------

_USER_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_BUILD_ID = uuid.uuid4()


def _contracts() -> list[dict]:
    return [
        {"contract_type": "blueprint", "content": "# Blueprint\nTest blueprint"},
        {"contract_type": "schema", "content": "# Schema\nTest schema"},
        {"contract_type": "stack", "content": "# Stack\nPython + FastAPI"},
        {"contract_type": "boundaries", "content": '# Boundaries\n{"layers": []}'},
        {"contract_type": "ui", "content": "# UI\nReact + Vite"},
        {"contract_type": "builder_contract", "content": "# Builder Contract\nRules"},
        {"contract_type": "manifesto", "content": "# Manifesto\nGovernance"},
        {"contract_type": "phases", "content": "# Phase 0\n## Phase 0 -- Init\n..."},
    ]


def _manifest_response(files: list[dict] | None = None) -> str:
    """Simulated manifest JSON response from planner LLM."""
    files = files or [
        {
            "path": "app/services/foo.py",
            "action": "create",
            "purpose": "Foo service",
            "depends_on": [],
            "context_files": [],
            "estimated_lines": 50,
            "language": "python",
        },
        {
            "path": "tests/test_foo.py",
            "action": "create",
            "purpose": "Tests for foo",
            "depends_on": ["app/services/foo.py"],
            "context_files": ["app/services/foo.py"],
            "estimated_lines": 40,
            "language": "python",
        },
    ]
    return json.dumps({"files": files})


# ---------------------------------------------------------------------------
# 21.7  _select_contracts_for_file
# ---------------------------------------------------------------------------


class TestSelectContractsForFile:
    """Tests for contract relevance mapping."""

    def test_app_file_gets_backend_contracts(self):
        contracts = _contracts()
        result = _select_contracts_for_file("app/services/foo.py", contracts)
        types = {c["contract_type"] for c in result}
        assert "blueprint" in types
        assert "schema" in types
        assert "stack" in types

    def test_test_file_gets_test_contracts(self):
        contracts = _contracts()
        result = _select_contracts_for_file("tests/test_foo.py", contracts)
        types = {c["contract_type"] for c in result}
        assert "blueprint" in types
        assert "schema" in types
        assert "boundaries" in types

    def test_frontend_file_gets_ui_contract(self):
        contracts = _contracts()
        result = _select_contracts_for_file("web/src/components/Button.tsx", contracts)
        types = {c["contract_type"] for c in result}
        assert "ui" in types
        assert "stack" in types

    def test_sql_file_only_gets_schema(self):
        contracts = _contracts()
        result = _select_contracts_for_file("db/migrations/001.sql", contracts)
        types = {c["contract_type"] for c in result}
        assert types == {"schema"}

    def test_unknown_path_gets_fallback(self):
        contracts = _contracts()
        result = _select_contracts_for_file("scripts/deploy.sh", contracts)
        types = {c["contract_type"] for c in result}
        assert "blueprint" in types
        assert "stack" in types

    def test_empty_contracts_returns_empty(self):
        result = _select_contracts_for_file("app/foo.py", [])
        assert result == []


# ---------------------------------------------------------------------------
# 21.5  _calculate_context_budget
# ---------------------------------------------------------------------------


class TestCalculateContextBudget:
    """Tests for token budget calculation."""

    def test_basic_budget(self):
        entry = {
            "path": "app/foo.py",
            "estimated_lines": 100,
            "context_files": ["app/bar.py"],
            "depends_on": [],
        }
        ctx = {"app/bar.py": "x" * 1000}
        result = _calculate_context_budget(entry, 5000, 3000, ctx)
        assert "files_to_include" in result
        assert "max_tokens" in result
        assert "app/bar.py" in result["files_to_include"]

    def test_max_tokens_scales_with_lines(self):
        entry_small = {"estimated_lines": 10, "context_files": [], "depends_on": []}
        entry_large = {"estimated_lines": 500, "context_files": [], "depends_on": []}
        small = _calculate_context_budget(entry_small, 0, 0, {})
        large = _calculate_context_budget(entry_large, 0, 0, {})
        assert small["max_tokens"] == 4096  # floor
        assert large["max_tokens"] > small["max_tokens"]

    def test_max_tokens_capped(self):
        entry = {"estimated_lines": 10000, "context_files": [], "depends_on": []}
        result = _calculate_context_budget(entry, 0, 0, {})
        assert result["max_tokens"] <= 16384

    def test_depends_on_prioritized(self):
        entry = {
            "path": "app/foo.py",
            "estimated_lines": 100,
            "context_files": ["app/ctx.py"],
            "depends_on": ["app/dep.py"],
        }
        ctx = {"app/dep.py": "dep content", "app/ctx.py": "ctx content"}
        result = _calculate_context_budget(entry, 0, 0, ctx)
        # depends_on should come first
        assert result["files_to_include"][0] == "app/dep.py"

    def test_missing_context_files_skipped(self):
        entry = {
            "path": "app/foo.py",
            "estimated_lines": 100,
            "context_files": ["missing.py"],
            "depends_on": [],
        }
        result = _calculate_context_budget(entry, 0, 0, {})
        assert result["files_to_include"] == []


# ---------------------------------------------------------------------------
#  _topological_sort
# ---------------------------------------------------------------------------


class TestTopologicalSort:
    """Tests for manifest dependency ordering."""

    def test_simple_dependency(self):
        files = [
            {"path": "B.py", "depends_on": ["A.py"]},
            {"path": "A.py", "depends_on": []},
        ]
        result = _topological_sort(files)
        paths = [f["path"] for f in result]
        assert paths.index("A.py") < paths.index("B.py")

    def test_no_dependencies(self):
        files = [
            {"path": "A.py", "depends_on": []},
            {"path": "B.py", "depends_on": []},
        ]
        result = _topological_sort(files)
        assert len(result) == 2

    def test_circular_dependency_fallback(self):
        files = [
            {"path": "A.py", "depends_on": ["B.py"]},
            {"path": "B.py", "depends_on": ["A.py"]},
        ]
        result = _topological_sort(files)
        # Should fall back to linear order
        assert len(result) == 2

    def test_chain_dependency(self):
        files = [
            {"path": "C.py", "depends_on": ["B.py"]},
            {"path": "A.py", "depends_on": []},
            {"path": "B.py", "depends_on": ["A.py"]},
        ]
        result = _topological_sort(files)
        paths = [f["path"] for f in result]
        assert paths.index("A.py") < paths.index("B.py")
        assert paths.index("B.py") < paths.index("C.py")


# ---------------------------------------------------------------------------
# 21.1  _generate_file_manifest
# ---------------------------------------------------------------------------


class TestGenerateFileManifest:
    """Tests for manifest generation via planner LLM."""

    @pytest.mark.asyncio
    async def test_successful_manifest(self):
        """Planner returns valid JSON → manifest parsed correctly."""
        mock_chat = AsyncMock(return_value={
            "text": _manifest_response(),
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="You are a planner..."):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            result = await build_service._generate_file_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                _contracts(),
                {"number": 1, "name": "Test", "deliverables": ["D1"]},
                "- app/\n- tests/",
            )

        assert result is not None
        assert len(result) == 2
        assert result[0]["path"] == "app/services/foo.py"
        assert result[0]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_manifest_with_fences(self):
        """Planner wraps JSON in markdown fences — still parsed."""
        fenced = f"```json\n{_manifest_response()}\n```"
        mock_chat = AsyncMock(return_value={
            "text": fenced,
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="You are a planner..."):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            result = await build_service._generate_file_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                _contracts(),
                {"number": 1, "name": "Test", "deliverables": ["D1"]},
                "",
            )

        assert result is not None
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_manifest_filters_invalid_paths(self):
        """Paths with '..' or starting with '/' are rejected."""
        bad_files = [
            {"path": "../escape.py", "action": "create", "purpose": "bad"},
            {"path": "/etc/passwd", "action": "create", "purpose": "bad"},
            {"path": "app/valid.py", "action": "create", "purpose": "good",
             "depends_on": [], "context_files": [], "estimated_lines": 50, "language": "python"},
        ]
        mock_chat = AsyncMock(return_value={
            "text": json.dumps({"files": bad_files}),
            "usage": {"input_tokens": 500, "output_tokens": 300},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="prompt"):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            result = await build_service._generate_file_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                _contracts(),
                {"number": 1, "name": "Test", "deliverables": []},
                "",
            )

        assert result is not None
        assert len(result) == 1
        assert result[0]["path"] == "app/valid.py"

    @pytest.mark.asyncio
    async def test_manifest_deduplicates(self):
        """Duplicate paths are rejected."""
        dup_files = [
            {"path": "app/foo.py", "action": "create", "purpose": "first"},
            {"path": "app/foo.py", "action": "create", "purpose": "duplicate"},
        ]
        mock_chat = AsyncMock(return_value={
            "text": json.dumps({"files": dup_files}),
            "usage": {"input_tokens": 500, "output_tokens": 300},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value="prompt"):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            result = await build_service._generate_file_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                _contracts(),
                {"number": 1, "name": "Test", "deliverables": []},
                "",
            )

        assert result is not None
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 21.2  _generate_single_file
# ---------------------------------------------------------------------------


class TestGenerateSingleFile:
    """Tests for independent per-file generation."""

    @pytest.mark.asyncio
    async def test_generates_file_content(self):
        """Single file call returns content and writes to disk."""
        mock_chat = AsyncMock(return_value={
            "text": "def hello():\n    return 'world'\n",
            "usage": {"input_tokens": 500, "output_tokens": 200},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch("app.services.tool_executor._exec_write_file", return_value="OK"):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            content = await build_service._generate_single_file(
                _BUILD_ID, _USER_ID, "sk-test",
                {
                    "path": "app/hello.py",
                    "purpose": "Say hello",
                    "language": "python",
                    "estimated_lines": 10,
                    "context_files": [],
                    "depends_on": [],
                },
                _contracts(),
                {},
                "Deliverables: say hello",
                "/tmp/test_project",
            )

        assert "def hello" in content
        assert content.endswith("\n")

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        """If model wraps output in markdown fences, they're stripped."""
        mock_chat = AsyncMock(return_value={
            "text": "```python\ndef foo():\n    pass\n```",
            "usage": {"input_tokens": 300, "output_tokens": 100},
        })

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch("app.services.tool_executor._exec_write_file", return_value="OK"):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            content = await build_service._generate_single_file(
                _BUILD_ID, _USER_ID, "sk-test",
                {"path": "foo.py", "purpose": "Test", "language": "python",
                 "estimated_lines": 5, "context_files": [], "depends_on": []},
                [], {}, "", "/tmp/test",
            )

        assert not content.startswith("```")
        assert not content.rstrip().endswith("```")

    @pytest.mark.asyncio
    async def test_records_cost(self):
        """Each file call records its token cost."""
        mock_chat = AsyncMock(return_value={
            "text": "content",
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        })
        mock_record = AsyncMock()

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.clients.llm_client.chat", mock_chat), \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch("app.services.tool_executor._exec_write_file", return_value="OK"):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = mock_record

            await build_service._generate_single_file(
                _BUILD_ID, _USER_ID, "sk-test",
                {"path": "foo.py", "purpose": "Test", "language": "python",
                 "estimated_lines": 5, "context_files": [], "depends_on": []},
                [], {}, "", "/tmp/test",
            )

        mock_record.assert_called_once()
        call_args = mock_record.call_args
        assert call_args[0][1] == "file:foo.py"


# ---------------------------------------------------------------------------
# 21.8  _generate_fix_manifest
# ---------------------------------------------------------------------------


class TestGenerateFixManifest:
    """Tests for fix manifest generation from recovery plan."""

    @pytest.mark.asyncio
    async def test_generates_fix_list(self):
        """Fix manifest returns list of files to fix."""
        fix_response = json.dumps({
            "fixes": [
                {
                    "path": "app/service.py",
                    "action": "modify",
                    "reason": "Missing error handling",
                    "context_files": ["app/models.py"],
                    "fix_instructions": "Add try/except",
                },
            ]
        })
        mock_chat = AsyncMock(return_value={
            "text": fix_response,
            "usage": {"input_tokens": 500, "output_tokens": 200},
        })

        with patch("app.clients.llm_client.chat", mock_chat):
            result = await build_service._generate_fix_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                "Fix error handling",
                {"app/service.py": "old code"},
                "Missing error handling in service.py",
                _contracts(),
            )

        assert result is not None
        assert len(result) == 1
        assert result[0]["path"] == "app/service.py"
        assert result[0]["action"] == "modify"
        assert result[0]["fix_instructions"] == "Add try/except"

    @pytest.mark.asyncio
    async def test_handles_invalid_json(self):
        """Invalid JSON returns None."""
        mock_chat = AsyncMock(return_value={
            "text": "This isn't JSON at all",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        })

        with patch("app.clients.llm_client.chat", mock_chat):
            result = await build_service._generate_fix_manifest(
                _BUILD_ID, _USER_ID, "sk-test",
                "plan", {}, "findings", [],
            )

        assert result is None


# ---------------------------------------------------------------------------
# 21.10  _run_build dispatcher
# ---------------------------------------------------------------------------


class TestBuildModeDispatcher:
    """Tests for build mode selection."""

    @pytest.mark.asyncio
    async def test_plan_execute_mode(self):
        """When BUILD_MODE=plan_execute and working_dir set → plan-execute path."""
        with patch.object(build_service.settings, "BUILD_MODE", "plan_execute"), \
             patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock, return_value={"build_spend_cap": None}), \
             patch("app.services.build_service._run_build_plan_execute", new_callable=AsyncMock) as mock_pe, \
             patch("app.services.build_service._run_build_conversation", new_callable=AsyncMock) as mock_conv:

            await build_service._run_build(
                _BUILD_ID, _PROJECT_ID, _USER_ID, [], "sk-test",
                working_dir="/tmp/test",
            )

        mock_pe.assert_called_once()
        mock_conv.assert_not_called()

    @pytest.mark.asyncio
    async def test_conversation_mode(self):
        """When BUILD_MODE=conversation → conversation path."""
        with patch.object(build_service.settings, "BUILD_MODE", "conversation"), \
             patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock, return_value={"build_spend_cap": None}), \
             patch("app.services.build_service._run_build_plan_execute", new_callable=AsyncMock) as mock_pe, \
             patch("app.services.build_service._run_build_conversation", new_callable=AsyncMock) as mock_conv:

            await build_service._run_build(
                _BUILD_ID, _PROJECT_ID, _USER_ID, [], "sk-test",
                working_dir="/tmp/test",
            )

        mock_conv.assert_called_once()
        mock_pe.assert_not_called()

    @pytest.mark.asyncio
    async def test_plan_execute_without_working_dir_falls_back(self):
        """Plan-execute without working_dir → falls back to conversation."""
        with patch.object(build_service.settings, "BUILD_MODE", "plan_execute"), \
             patch("app.services.build_service.get_user_by_id", new_callable=AsyncMock, return_value={"build_spend_cap": None}), \
             patch("app.services.build_service._run_build_plan_execute", new_callable=AsyncMock) as mock_pe, \
             patch("app.services.build_service._run_build_conversation", new_callable=AsyncMock) as mock_conv:

            await build_service._run_build(
                _BUILD_ID, _PROJECT_ID, _USER_ID, [], "sk-test",
            )

        mock_conv.assert_called_once()
        mock_pe.assert_not_called()


# ---------------------------------------------------------------------------
# 21.3  _verify_phase_output (smoke test — relies on tool_executor)
# ---------------------------------------------------------------------------


class TestVerifyPhaseOutput:
    """Tests for post-generation verification."""

    @pytest.mark.asyncio
    async def test_clean_verification(self):
        """All files pass syntax → clean result."""
        mock_check = AsyncMock(return_value="No syntax errors in foo.py")

        manifest = [
            {"path": "app/foo.py", "language": "python", "status": "done"},
        ]

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch("app.services.tool_executor._exec_check_syntax", mock_check):

            mock_repo.append_build_log = AsyncMock()

            result = await build_service._verify_phase_output(
                _BUILD_ID, _USER_ID, "sk-test",
                manifest, "/tmp/test", _contracts(),
            )

        assert result["syntax_errors"] == 0

    @pytest.mark.asyncio
    async def test_syntax_error_detected(self):
        """Syntax error in a file → counted in result."""
        call_count = 0

        async def mock_check(inp, wd):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "Syntax error in foo.py:10: invalid syntax"
            return "Syntax error in foo.py:10: invalid syntax"

        mock_gen = AsyncMock(return_value="fixed content")

        manifest = [
            {"path": "app/foo.py", "language": "python", "status": "done",
             "purpose": "Foo", "context_files": [], "depends_on": [],
             "estimated_lines": 10},
        ]

        with patch("app.services.build._state.build_repo") as mock_repo, \
             patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock), \
             patch("app.services.tool_executor._exec_check_syntax", side_effect=mock_check), \
             patch("app.services.build_service._generate_single_file", mock_gen):

            mock_repo.append_build_log = AsyncMock()
            mock_repo.record_build_cost = AsyncMock()

            result = await build_service._verify_phase_output(
                _BUILD_ID, _USER_ID, "sk-test",
                manifest, "/tmp/test", _contracts(),
            )

        # Still has error because mock_check always returns error
        assert result["syntax_errors"] == 1
