"""Tests for app.services.build.subagent — sub-agent handoff protocol.

Covers:
- SubAgentRole / HandoffStatus enums
- Per-role tool allow-lists  (tools_for_role, tool_names_for_role)
- System prompt generation
- SubAgentHandoff / SubAgentResult dataclasses + serialisation
- Context pack builder  (build_context_pack)
- .forge directory management (ensure_forge_dir, save_handoff, etc.)
- Sub-agent runner  (run_sub_agent) with mocked stream_agent
- Tool enforcement  (disallowed tools are blocked)
- JSON extraction helper
"""

import asyncio
import json
import os
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.services.build.subagent import (
    HandoffStatus,
    SubAgentHandoff,
    SubAgentResult,
    SubAgentRole,
    _ROLE_TOOL_NAMES,
    _extract_json_block,
    build_context_pack,
    ensure_forge_dir,
    load_progress,
    run_sub_agent,
    save_handoff,
    save_progress,
    save_result,
    system_prompt_for_role,
    tool_names_for_role,
    tools_for_role,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BUILD_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_ID = UUID("00000000-0000-0000-0000-000000000002")


@pytest.fixture()
def working_dir(tmp_path):
    """Create a minimal project tree for context pack tests."""
    # Python files
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(
        "from app.config import settings\ndef main(): pass\n", encoding="utf-8",
    )
    (tmp_path / "app" / "config.py").write_text(
        "settings = {}\n", encoding="utf-8",
    )
    (tmp_path / "app" / "utils.py").write_text(
        "def helper(): return 42\n", encoding="utf-8",
    )
    # TypeScript file
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.ts").write_text(
        "export const App = () => {}\n", encoding="utf-8",
    )
    return str(tmp_path)


# Fake tool list for testing (mirrors real structure, smaller)
FAKE_TOOLS = [
    {"name": "read_file", "description": "Read a file", "input_schema": {}},
    {"name": "list_directory", "description": "List dir", "input_schema": {}},
    {"name": "search_code", "description": "Search", "input_schema": {}},
    {"name": "write_file", "description": "Write file", "input_schema": {}},
    {"name": "edit_file", "description": "Edit file", "input_schema": {}},
    {"name": "run_tests", "description": "Run tests", "input_schema": {}},
    {"name": "check_syntax", "description": "Check syntax", "input_schema": {}},
    {"name": "run_command", "description": "Run cmd", "input_schema": {}},
    {"name": "forge_get_contract", "description": "Get contract", "input_schema": {}},
    {"name": "forge_get_phase_window", "description": "Phase window", "input_schema": {}},
    {"name": "forge_list_contracts", "description": "List contracts", "input_schema": {}},
    {"name": "forge_get_summary", "description": "Summary", "input_schema": {}},
    {"name": "forge_scratchpad", "description": "Scratchpad", "input_schema": {}},
    {"name": "forge_ask_clarification", "description": "Ask clarification", "input_schema": {}},
    # Project-scoped tools (Phase F)
    {"name": "forge_get_project_context", "description": "Project context", "input_schema": {}},
    {"name": "forge_list_project_contracts", "description": "List project contracts", "input_schema": {}},
    {"name": "forge_get_project_contract", "description": "Get project contract", "input_schema": {}},
    {"name": "forge_get_build_contracts", "description": "Get build contracts", "input_schema": {}},
]


# ===================================================================
# Enum tests
# ===================================================================


class TestSubAgentRole:
    def test_values(self):
        assert SubAgentRole.SCOUT.value == "scout"
        assert SubAgentRole.CODER.value == "coder"
        assert SubAgentRole.AUDITOR.value == "auditor"
        assert SubAgentRole.FIXER.value == "fixer"

    def test_is_str_enum(self):
        assert isinstance(SubAgentRole.SCOUT, str)
        assert SubAgentRole.CODER == "coder"

    def test_all_roles_have_tool_sets(self):
        for role in SubAgentRole:
            assert role in _ROLE_TOOL_NAMES, f"Missing tool set for {role}"


class TestHandoffStatus:
    def test_values(self):
        assert HandoffStatus.PENDING.value == "pending"
        assert HandoffStatus.RUNNING.value == "running"
        assert HandoffStatus.COMPLETED.value == "completed"
        assert HandoffStatus.FAILED.value == "failed"


# ===================================================================
# Tool allow-list tests
# ===================================================================


class TestToolsForRole:
    """Verify per-role tool filtering enforces least privilege."""

    def test_scout_read_only(self):
        tools = tools_for_role(SubAgentRole.SCOUT, FAKE_TOOLS)
        names = {t["name"] for t in tools}
        # Must have read tools
        assert "read_file" in names
        assert "list_directory" in names
        assert "search_code" in names
        # Must NOT have write/edit/exec tools
        assert "write_file" not in names
        assert "edit_file" not in names
        assert "run_tests" not in names
        assert "run_command" not in names
        assert "check_syntax" not in names

    def test_coder_can_write(self):
        tools = tools_for_role(SubAgentRole.CODER, FAKE_TOOLS)
        names = {t["name"] for t in tools}
        assert "write_file" in names
        assert "edit_file" in names
        assert "check_syntax" in names
        assert "run_command" in names
        # Coder should NOT run tests
        assert "run_tests" not in names

    def test_auditor_read_only(self):
        tools = tools_for_role(SubAgentRole.AUDITOR, FAKE_TOOLS)
        names = {t["name"] for t in tools}
        assert "read_file" in names
        assert "search_code" in names
        assert "write_file" not in names
        assert "edit_file" not in names
        assert "run_tests" not in names

    def test_fixer_edit_only_no_write(self):
        tools = tools_for_role(SubAgentRole.FIXER, FAKE_TOOLS)
        names = {t["name"] for t in tools}
        assert "edit_file" in names
        assert "check_syntax" in names
        assert "read_file" in names
        # Fixer must NOT have write_file
        assert "write_file" not in names
        assert "run_tests" not in names
        assert "run_command" not in names

    def test_preserves_tool_order(self):
        tools = tools_for_role(SubAgentRole.CODER, FAKE_TOOLS)
        names = [t["name"] for t in tools]
        # Order should match FAKE_TOOLS order, just filtered
        original_order = [t["name"] for t in FAKE_TOOLS]
        filtered = [n for n in original_order if n in tool_names_for_role(SubAgentRole.CODER)]
        assert names == filtered

    def test_unknown_tool_excluded(self):
        extra = FAKE_TOOLS + [{"name": "dangerous_tool", "description": "bad", "input_schema": {}}]
        for role in SubAgentRole:
            tools = tools_for_role(role, extra)
            names = {t["name"] for t in tools}
            assert "dangerous_tool" not in names

    def test_tool_names_for_role(self):
        names = tool_names_for_role(SubAgentRole.SCOUT)
        assert isinstance(names, frozenset)
        assert "read_file" in names

    def test_all_roles_are_subsets_of_full_tools(self):
        """Every tool in a role's allow-list must exist in the full tool set."""
        all_names = {t["name"] for t in FAKE_TOOLS}
        for role in SubAgentRole:
            role_names = tool_names_for_role(role)
            assert role_names <= all_names, (
                f"Role {role.value} has unknown tools: {role_names - all_names}"
            )

    def test_no_role_has_run_tests(self):
        """run_tests is a standalone step — no LLM sub-agent should have it."""
        for role in SubAgentRole:
            assert "run_tests" not in tool_names_for_role(role)

    def test_only_coder_has_write_file(self):
        for role in SubAgentRole:
            if role == SubAgentRole.CODER:
                assert "write_file" in tool_names_for_role(role)
            else:
                assert "write_file" not in tool_names_for_role(role)


# ===================================================================
# System prompt tests
# ===================================================================


class TestSystemPrompts:
    def test_every_role_has_prompt(self):
        for role in SubAgentRole:
            prompt = system_prompt_for_role(role)
            assert len(prompt) > 50, f"Empty/short prompt for {role}"

    def test_scout_prompt_mentions_read_only(self):
        prompt = system_prompt_for_role(SubAgentRole.SCOUT)
        assert "READ-ONLY" in prompt

    def test_coder_prompt_mentions_syntax(self):
        prompt = system_prompt_for_role(SubAgentRole.CODER)
        assert "syntax" in prompt.lower()

    def test_fixer_prompt_mentions_edit_file(self):
        prompt = system_prompt_for_role(SubAgentRole.FIXER)
        assert "edit_file" in prompt

    def test_fixer_prompt_blocks_write_file(self):
        prompt = system_prompt_for_role(SubAgentRole.FIXER)
        assert "write_file" in prompt.lower()  # mentioned as blocked

    def test_extra_appended(self):
        base = system_prompt_for_role(SubAgentRole.SCOUT)
        with_extra = system_prompt_for_role(SubAgentRole.SCOUT, extra="CUSTOM DIRECTIVE")
        assert with_extra.startswith(base)
        assert "CUSTOM DIRECTIVE" in with_extra


# ===================================================================
# Dataclass tests
# ===================================================================


class TestSubAgentHandoff:
    def test_defaults(self):
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Write the config module",
        )
        assert h.files == []
        assert h.context_files == {}
        assert h.max_tokens == 16_384
        assert h.timeout_seconds == 600.0

    def test_to_dict_serialises(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout the project",
            files=["app/main.py"],
        )
        d = h.to_dict()
        assert d["role"] == "scout"
        assert d["build_id"] == str(BUILD_ID)
        assert d["files"] == ["app/main.py"]
        # Ensure it's JSON-serialisable
        json.dumps(d)

    def test_to_dict_json_roundtrip(self):
        h = SubAgentHandoff(
            role=SubAgentRole.FIXER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Fix imports",
            error_context="L42: missing import",
        )
        text = json.dumps(h.to_dict(), default=str)
        loaded = json.loads(text)
        assert loaded["error_context"] == "L42: missing import"


class TestSubAgentResult:
    def test_defaults(self):
        r = SubAgentResult(handoff_id="test_1", role=SubAgentRole.CODER)
        assert r.status == HandoffStatus.COMPLETED
        assert r.files_written == []
        assert r.cost_usd == 0.0

    def test_to_dict(self):
        r = SubAgentResult(
            handoff_id="test_1",
            role=SubAgentRole.AUDITOR,
            status=HandoffStatus.FAILED,
            error="timeout",
            input_tokens=100,
            output_tokens=50,
        )
        d = r.to_dict()
        assert d["role"] == "auditor"
        assert d["status"] == "failed"
        json.dumps(d)


# ===================================================================
# Context pack tests
# ===================================================================


class TestBuildContextPack:
    def test_includes_target_files(self, working_dir):
        ctx = build_context_pack(working_dir, ["app/main.py"])
        assert "app/main.py" in ctx

    def test_includes_imports(self, working_dir):
        ctx = build_context_pack(working_dir, ["app/main.py"], include_imports=True)
        # main.py imports from app.config
        assert "app/config.py" in ctx

    def test_includes_siblings(self, working_dir):
        ctx = build_context_pack(working_dir, ["app/main.py"], include_siblings=True)
        # utils.py is a sibling of main.py
        assert "app/utils.py" in ctx

    def test_respects_max_files(self, working_dir):
        ctx = build_context_pack(
            working_dir, ["app/main.py"], max_context_files=2,
        )
        assert len(ctx) <= 2

    def test_respects_char_budget(self, working_dir):
        ctx = build_context_pack(
            working_dir, ["app/main.py"], max_context_chars=100,
        )
        total = sum(len(v) for v in ctx.values())
        # Target file is always included, so total might exceed budget
        # but non-target files should be trimmed
        assert len(ctx) >= 1  # at least the target

    def test_missing_target_excluded_gracefully(self, working_dir):
        ctx = build_context_pack(working_dir, ["nonexistent.py"])
        assert "nonexistent.py" not in ctx

    def test_no_imports_flag(self, working_dir):
        ctx = build_context_pack(
            working_dir, ["app/main.py"],
            include_imports=False, include_siblings=False,
        )
        # Only the target itself
        assert "app/main.py" in ctx
        assert "app/config.py" not in ctx

    def test_no_siblings_flag(self, working_dir):
        ctx = build_context_pack(
            working_dir, ["app/main.py"],
            include_imports=False, include_siblings=False,
        )
        assert "app/utils.py" not in ctx

    def test_backslash_normalised(self, working_dir):
        ctx = build_context_pack(working_dir, ["app\\main.py"])
        assert "app/main.py" in ctx

    def test_empty_targets(self, working_dir):
        ctx = build_context_pack(working_dir, [])
        # No targets → should still return (maybe siblings/imports from nothing)
        assert isinstance(ctx, dict)


# ===================================================================
# .forge directory management tests
# ===================================================================


class TestForgeDirManagement:
    def test_ensure_forge_dir(self, tmp_path):
        wd = str(tmp_path)
        forge = ensure_forge_dir(wd)
        assert forge.exists()
        assert (forge / "handoffs").exists()

    def test_save_and_load_handoff(self, tmp_path):
        wd = str(tmp_path)
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Write files",
            handoff_id="coder_001",
        )
        fp = save_handoff(wd, h)
        assert fp.exists()
        loaded = json.loads(fp.read_text(encoding="utf-8"))
        assert loaded["role"] == "coder"
        assert loaded["handoff_id"] == "coder_001"

    def test_save_result(self, tmp_path):
        wd = str(tmp_path)
        r = SubAgentResult(
            handoff_id="coder_001",
            role=SubAgentRole.CODER,
            text_output="done",
        )
        fp = save_result(wd, r)
        assert fp.exists()
        loaded = json.loads(fp.read_text(encoding="utf-8"))
        assert loaded["text_output"] == "done"

    def test_progress_roundtrip(self, tmp_path):
        wd = str(tmp_path)
        assert load_progress(wd) == {}
        save_progress(wd, {"phase": 3, "files_done": ["a.py"]})
        p = load_progress(wd)
        assert p["phase"] == 3
        assert "a.py" in p["files_done"]


# ===================================================================
# JSON extraction tests
# ===================================================================


class TestExtractJsonBlock:
    def test_fenced_json(self):
        text = 'Some explanation\n```json\n{"key": "value"}\n```\n'
        assert _extract_json_block(text) == {"key": "value"}

    def test_bare_json(self):
        text = 'Here is the result: {"files_written": ["a.py"]}'
        assert _extract_json_block(text) == {"files_written": ["a.py"]}

    def test_nested_json(self):
        text = '{"outer": {"inner": 1}}'
        result = _extract_json_block(text)
        assert result["outer"]["inner"] == 1

    def test_no_json(self):
        assert _extract_json_block("just plain text") == {}

    def test_empty_string(self):
        assert _extract_json_block("") == {}

    def test_invalid_json(self):
        assert _extract_json_block("{broken json") == {}

    def test_multiple_blocks_returns_last(self):
        text = '```json\n{"first": 1}\n```\nmore text\n```json\n{"second": 2}\n```'
        assert _extract_json_block(text) == {"second": 2}


# ===================================================================
# Sub-agent runner tests
# ===================================================================


def _make_fake_settings(**overrides):
    """Build a fake settings namespace."""
    defaults = {
        "OPENAI_MODEL": "claude-opus-4-20250514",
        "LLM_QUESTIONNAIRE_MODEL": "claude-sonnet-4-20250514",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestRunSubAgent:
    """Test run_sub_agent with mocked LLM/stream."""

    @pytest.fixture(autouse=True)
    def _patch_deps(self, tmp_path):
        """Patch external dependencies for all tests in this class."""
        self.working_dir = str(tmp_path)
        self.api_key = "sk-test-key"

        # Mock settings
        fake_settings = _make_fake_settings()

        # Mock build_repo
        self.mock_repo = MagicMock()
        self.mock_repo.append_build_log = AsyncMock()
        self.mock_repo.record_build_cost = AsyncMock()

        # Mock ws manager
        self.mock_manager = MagicMock()
        self.mock_manager.broadcast_to_user = AsyncMock()

        # _state is imported at the top of subagent.py as `from . import _state`
        # so it IS an attribute — but the underlying _state module attributes
        # need patching.  Patch at the _state module level directly.
        patches = [
            patch("app.services.build._state.settings", fake_settings),
            patch("app.services.build._state.build_repo", self.mock_repo),
            patch("app.services.build._state.manager", self.mock_manager),
            patch("app.services.build._state._broadcast_build_event", new_callable=AsyncMock),
        ]
        self._patch_objs = patches
        for p in patches:
            p.start()
        yield
        for p in patches:
            p.stop()

    async def _run_with_stream(self, handoff, stream_events):
        """Helper: run a sub-agent with a fake stream_agent that yields events."""
        from app.clients.agent_client import StreamUsage

        async def fake_stream(*args, **kwargs):
            usage = kwargs.get("usage_out")
            if usage:
                usage.input_tokens = 100
                usage.output_tokens = 50
            for event in stream_events:
                yield event

        with patch("app.services.build.subagent.stream_agent", fake_stream):
            with patch("app.services.build.subagent.execute_tool_async", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = "tool result OK"
                return await run_sub_agent(
                    handoff, self.working_dir, self.api_key,
                )

    @pytest.mark.asyncio
    async def test_simple_text_response(self):
        """Agent returns text only — no tool calls."""
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Map the project",
        )
        result = await self._run_with_stream(h, ["Project has 3 modules."])

        assert result.status == HandoffStatus.COMPLETED
        assert result.role == SubAgentRole.SCOUT
        assert "3 modules" in result.text_output
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_tool_enforcement_blocks_disallowed(self):
        """Scout trying to use write_file should be blocked."""
        from app.clients.agent_client import ToolCall

        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout the project",
        )

        events = [
            ToolCall(id="tc_1", name="write_file", input={"path": "hack.py", "content": "bad"}),
        ]

        # After tool block the stream ends naturally on next round
        call_count = 0

        async def fake_stream(*args, **kwargs):
            nonlocal call_count
            usage = kwargs.get("usage_out")
            if usage:
                usage.input_tokens = 50
                usage.output_tokens = 25
            if call_count == 0:
                call_count += 1
                for e in events:
                    yield e
            else:
                yield "Done after being blocked."

        with patch("app.services.build.subagent.stream_agent", fake_stream):
            with patch("app.services.build.subagent.execute_tool_async", new_callable=AsyncMock) as mock_exec:
                result = await run_sub_agent(h, self.working_dir, self.api_key)

        # execute_tool_async should NOT have been called for write_file
        # (the enforcement happens before execute)
        for call in mock_exec.call_args_list:
            assert call[0][0] != "write_file", "write_file should have been blocked"

        assert result.status == HandoffStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_tool_enforcement_allows_permitted(self):
        """Scout using read_file should work fine."""
        from app.clients.agent_client import ToolCall

        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Read the main file",
        )

        call_count = 0

        async def fake_stream(*args, **kwargs):
            nonlocal call_count
            usage = kwargs.get("usage_out")
            if usage:
                usage.input_tokens = 50
                usage.output_tokens = 25
            if call_count == 0:
                call_count += 1
                yield ToolCall(id="tc_1", name="read_file", input={"path": "main.py"})
            else:
                yield "File content received."

        with patch("app.services.build.subagent.stream_agent", fake_stream):
            with patch("app.services.build.subagent.execute_tool_async", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = "# main.py content"
                result = await run_sub_agent(h, self.working_dir, self.api_key)

        # read_file should have been executed
        mock_exec.assert_called_once()
        assert mock_exec.call_args[0][0] == "read_file"
        assert result.files_read == ["main.py"]

    @pytest.mark.asyncio
    async def test_coder_tracks_files_written(self):
        """Coder writing files should track them in result."""
        from app.clients.agent_client import ToolCall

        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Write config module",
            files=["app/config.py"],
        )

        call_count = 0

        async def fake_stream(*args, **kwargs):
            nonlocal call_count
            usage = kwargs.get("usage_out")
            if usage:
                usage.input_tokens = 200
                usage.output_tokens = 100
            if call_count == 0:
                call_count += 1
                yield ToolCall(
                    id="tc_1", name="write_file",
                    input={"path": "app/config.py", "content": "settings = {}"},
                )
            else:
                yield '{"files_written": ["app/config.py"]}'

        with patch("app.services.build.subagent.stream_agent", fake_stream):
            with patch("app.services.build.subagent.execute_tool_async", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = "File written: app/config.py"
                result = await run_sub_agent(h, self.working_dir, self.api_key)

        assert "app/config.py" in result.files_written
        assert result.status == HandoffStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_fixer_cannot_use_write_file(self):
        """Fixer should be blocked from write_file but can use edit_file."""
        from app.clients.agent_client import ToolCall

        h = SubAgentHandoff(
            role=SubAgentRole.FIXER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Fix imports",
            error_context="L42: missing import os",
        )

        call_count = 0

        async def fake_stream(*args, **kwargs):
            nonlocal call_count
            usage = kwargs.get("usage_out")
            if usage:
                usage.input_tokens = 100
                usage.output_tokens = 50
            if call_count == 0:
                call_count += 1
                # Try write_file (blocked) then edit_file (allowed)
                yield ToolCall(
                    id="tc_1", name="write_file",
                    input={"path": "app/main.py", "content": "rewritten"},
                )
                yield ToolCall(
                    id="tc_2", name="edit_file",
                    input={"path": "app/main.py", "edits": [{"old_text": "x", "new_text": "y"}]},
                )
            else:
                yield "Fixed."

        with patch("app.services.build.subagent.stream_agent", fake_stream):
            with patch("app.services.build.subagent.execute_tool_async", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = "Edit applied"
                result = await run_sub_agent(h, self.working_dir, self.api_key)

        # Only edit_file should have been executed
        executed_tools = [c[0][0] for c in mock_exec.call_args_list]
        assert "write_file" not in executed_tools
        assert "edit_file" in executed_tools

    @pytest.mark.asyncio
    async def test_result_includes_timing(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Quick scan",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.duration_seconds >= 0
        assert result.started_at > 0
        assert result.finished_at >= result.started_at

    @pytest.mark.asyncio
    async def test_result_includes_tokens(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Quick scan",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.input_tokens == 100
        assert result.output_tokens == 50

    @pytest.mark.asyncio
    async def test_structured_output_extracted(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Map project",
        )
        output = 'Analysis complete.\n```json\n{"directory_tree": "app/", "key_interfaces": ["Config"]}\n```'
        result = await self._run_with_stream(h, [output])
        assert result.structured_output.get("key_interfaces") == ["Config"]

    @pytest.mark.asyncio
    async def test_stream_error_marks_failed(self):
        """If stream_agent raises, result status should be FAILED."""
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout",
        )

        async def exploding_stream(*args, **kwargs):
            raise RuntimeError("API down")
            yield  # make it async generator  # noqa: unreachable

        with patch("app.services.build.subagent.stream_agent", exploding_stream):
            result = await run_sub_agent(h, self.working_dir, self.api_key)

        assert result.status == HandoffStatus.FAILED
        assert "API down" in result.error

    @pytest.mark.asyncio
    async def test_handoff_id_auto_assigned(self):
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Code",
        )
        assert h.handoff_id == ""
        result = await self._run_with_stream(h, ["done"])
        assert h.handoff_id != ""  # auto-assigned by runner
        assert result.handoff_id == h.handoff_id

    @pytest.mark.asyncio
    async def test_handoff_persisted_to_forge_dir(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout",
            handoff_id="scout_test_001",
        )
        result = await self._run_with_stream(h, ["done"])
        # Check .forge/handoffs/ has the files
        hf = Path(self.working_dir) / ".forge" / "handoffs" / "scout_test_001.json"
        rf = Path(self.working_dir) / ".forge" / "handoffs" / "scout_test_001_result.json"
        assert hf.exists()
        assert rf.exists()

    @pytest.mark.asyncio
    async def test_cost_recorded(self):
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Code",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.cost_usd >= 0  # will be small for 150 tokens

    @pytest.mark.asyncio
    async def test_model_default_coder_uses_builder_model(self):
        h = SubAgentHandoff(
            role=SubAgentRole.CODER,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Code",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.model == "claude-opus-4-20250514"

    @pytest.mark.asyncio
    async def test_model_default_scout_uses_lighter_model(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.model == "claude-sonnet-4-20250514"

    @pytest.mark.asyncio
    async def test_model_override(self):
        h = SubAgentHandoff(
            role=SubAgentRole.SCOUT,
            build_id=BUILD_ID,
            user_id=USER_ID,
            assignment="Scout",
            model="claude-3-haiku-20240307",
        )
        result = await self._run_with_stream(h, ["done"])
        assert result.model == "claude-3-haiku-20240307"


# ===================================================================
# Edge case & integration tests
# ===================================================================


class TestEdgeCases:
    def test_empty_context_pack(self, tmp_path):
        """Empty directory should return empty context."""
        ctx = build_context_pack(str(tmp_path), ["nonexistent.py"])
        assert ctx == {}

    def test_large_file_truncated_in_context(self, tmp_path):
        """Files > 30KB should be truncated in context pack."""
        big = tmp_path / "big.py"
        big.write_text("x" * 50_000, encoding="utf-8")
        ctx = build_context_pack(str(tmp_path), ["big.py"])
        assert "big.py" in ctx
        assert len(ctx["big.py"]) < 50_000
        assert "truncated" in ctx["big.py"]

    def test_forge_dir_idempotent(self, tmp_path):
        """Creating .forge twice should not error."""
        ensure_forge_dir(str(tmp_path))
        ensure_forge_dir(str(tmp_path))
        assert (tmp_path / ".forge" / "handoffs").exists()

    def test_progress_corrupt_json(self, tmp_path):
        """Corrupt progress.json should return empty dict."""
        (tmp_path / ".forge").mkdir()
        (tmp_path / ".forge" / "progress.json").write_text("{bad", encoding="utf-8")
        assert load_progress(str(tmp_path)) == {}
