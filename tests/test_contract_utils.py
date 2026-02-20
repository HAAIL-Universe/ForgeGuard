"""Tests for app.services.project.contract_utils — tool-use contract generation.

Covers:
  - strip_code_fences / extract_text_from_blocks (unit)
  - build_tool_use_system_prompt (scout + greenfield variants)
  - execute_scout_tool (every tool name)
  - execute_greenfield_tool (every tool name)
  - generate_contract_with_tools (multi-turn loop, submit, fallback, max_turns)
  - CONTEXT_TOOLS_SCOUT / CONTEXT_TOOLS_GREENFIELD schema checks
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.project.contract_utils import (
    CONTEXT_TOOLS_GREENFIELD,
    CONTEXT_TOOLS_SCOUT,
    MINI_DEFAULTS,
    _is_placeholder,
    build_tool_use_system_prompt,
    execute_greenfield_tool,
    execute_scout_tool,
    extract_text_from_blocks,
    generate_contract_with_tools,
    strip_code_fences,
)


# ---------------------------------------------------------------------------
# Fixtures — shared sample data
# ---------------------------------------------------------------------------

_STACK = {"backend": {"framework": "FastAPI"}, "frontend": {"framework": "React"}}

_EXECUTIVE_BRIEF = {
    "health_grade": "B",
    "headline": "Solid foundation, minor issues",
    "top_priorities": ["Upgrade deps"],
}

_MIGRATION_TASKS = [
    {"id": "T1", "from_state": "fastapi 0.95", "to_state": "fastapi 0.110", "priority": "high"}
]

_DOSSIER = {"executive_summary": "A FastAPI backend.", "intent": "Manage users."}

_CHECKS = [{"code": "A0", "name": "License", "result": "PASS"}]

_ARCH = {"services": ["user_service.py"], "repos": ["user_repo.py"]}

_CONTEXT_DATA = {
    "stack_profile": _STACK,
    "executive_brief": _EXECUTIVE_BRIEF,
    "migration_tasks": _MIGRATION_TASKS,
    "dossier": _DOSSIER,
    "checks": _CHECKS,
    "architecture": _ARCH,
}

_PRIOR_CONTRACTS = {
    "manifesto": "# Manifesto\nThis project...",
    "blueprint": "# Blueprint\nArchitecture...",
}

_ANSWERS_DATA = {
    "product_intent": {"description": "A task manager", "goals": ["Fast", "Simple"]},
    "tech_stack": {"backend": "Python", "frontend": "React", "database": "PostgreSQL"},
    "ui_requirements": {"theme": "dark", "responsive": True},
    "api_endpoints": ["/tasks", "/users", "/auth"],
    "database_schema": {"tables": ["tasks", "users"]},
    "architectural_boundaries": {"monolith": True},
    "deployment_target": {"platform": "AWS", "scale": "small"},
    "project_name": "TaskFlow",
    "project_description": "A simple task manager",
}


# ===================================================================
# strip_code_fences
# ===================================================================


class TestStripCodeFences:
    def test_no_fences(self):
        assert strip_code_fences("hello\nworld") == "hello\nworld"

    def test_plain_fences(self):
        assert strip_code_fences("```\nfoo\nbar\n```") == "foo\nbar"

    def test_language_fences(self):
        assert strip_code_fences("```markdown\n# Title\n```") == "# Title"

    def test_only_opening(self):
        result = strip_code_fences("```\ncontent without closing")
        assert result == "content without closing"

    def test_empty_input(self):
        assert strip_code_fences("") == ""


# ===================================================================
# extract_text_from_blocks
# ===================================================================


class TestExtractTextFromBlocks:
    def test_text_blocks(self):
        blocks = [
            {"type": "text", "text": "Hello"},
            {"type": "text", "text": "World"},
        ]
        assert extract_text_from_blocks(blocks) == "Hello\nWorld"

    def test_ignores_tool_use_blocks(self):
        blocks = [
            {"type": "text", "text": "Before"},
            {"type": "tool_use", "name": "get_stack_profile", "input": {}},
            {"type": "text", "text": "After"},
        ]
        assert extract_text_from_blocks(blocks) == "Before\nAfter"

    def test_empty_blocks(self):
        assert extract_text_from_blocks([]) == ""

    def test_no_text_blocks(self):
        blocks = [{"type": "tool_use", "name": "foo", "input": {}}]
        assert extract_text_from_blocks(blocks) == ""


# ===================================================================
# build_tool_use_system_prompt
# ===================================================================


class TestBuildToolUseSystemPrompt:
    def test_scout_mode_includes_repo_name(self):
        prompt = build_tool_use_system_prompt("stack", repo_name="user/repo", mode="scout")
        assert "user/repo" in prompt
        assert "EXISTING CODEBASE MODE" in prompt

    def test_scout_mode_includes_instructions(self):
        prompt = build_tool_use_system_prompt("manifesto", repo_name="r", mode="scout")
        assert "manifesto" in prompt.lower()

    def test_scout_mode_includes_tool_guidance(self):
        prompt = build_tool_use_system_prompt("stack", repo_name="r", mode="scout")
        assert "get_stack_profile" in prompt
        assert "get_renovation_priorities" in prompt
        assert "submit_contract" in prompt

    def test_greenfield_mode_includes_project_name(self):
        prompt = build_tool_use_system_prompt(
            "stack", project_name="MyApp", mode="greenfield"
        )
        assert "MyApp" in prompt
        assert "EXISTING CODEBASE" not in prompt

    def test_greenfield_mode_includes_greenfield_tools(self):
        prompt = build_tool_use_system_prompt(
            "stack", project_name="MyApp", mode="greenfield"
        )
        assert "get_project_intent" in prompt
        assert "get_technical_preferences" in prompt

    def test_mini_variant_used_when_flag_set(self):
        # mini=True should look for _mini instruction variant first
        prompt_normal = build_tool_use_system_prompt("stack", repo_name="r", mode="scout")
        prompt_mini = build_tool_use_system_prompt(
            "stack", repo_name="r", mode="scout", mini=True
        )
        # Both should be valid prompts (may differ if _mini exists)
        assert "stack" in prompt_normal.lower()
        assert "stack" in prompt_mini.lower()

    def test_unknown_contract_type_uses_fallback_instructions(self):
        prompt = build_tool_use_system_prompt(
            "unknown_type", repo_name="r", mode="scout"
        )
        assert "unknown_type" in prompt

    @patch("app.services.project.contract_utils._load_generic_template")
    def test_includes_structural_reference(self, mock_load):
        mock_load.return_value = "# Example Template Content"
        prompt = build_tool_use_system_prompt("stack", repo_name="r", mode="scout")
        assert "STRUCTURAL REFERENCE" in prompt
        assert "Example Template Content" in prompt

    @patch("app.services.project.contract_utils._load_generic_template")
    def test_no_reference_when_template_missing(self, mock_load):
        mock_load.return_value = None
        prompt = build_tool_use_system_prompt("stack", repo_name="r", mode="scout")
        assert "STRUCTURAL REFERENCE" not in prompt


# ===================================================================
# execute_scout_tool
# ===================================================================


class TestExecuteScoutTool:
    def test_get_stack_profile(self):
        result = json.loads(
            execute_scout_tool("get_stack_profile", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert result["backend"]["framework"] == "FastAPI"

    def test_get_renovation_priorities(self):
        result = json.loads(
            execute_scout_tool("get_renovation_priorities", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert result["health_grade"] == "B"

    def test_get_migration_tasks(self):
        result = json.loads(
            execute_scout_tool("get_migration_tasks", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert len(result) == 1
        assert result[0]["id"] == "T1"

    def test_get_project_dossier(self):
        result = json.loads(
            execute_scout_tool("get_project_dossier", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert "FastAPI" in result["executive_summary"]

    def test_get_compliance_checks(self):
        result = json.loads(
            execute_scout_tool("get_compliance_checks", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert result[0]["code"] == "A0"

    def test_get_architecture_map(self):
        result = json.loads(
            execute_scout_tool("get_architecture_map", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert "services" in result

    def test_get_prior_contract_found(self):
        result = execute_scout_tool(
            "get_prior_contract",
            {"contract_type": "manifesto"},
            _CONTEXT_DATA,
            _PRIOR_CONTRACTS,
        )
        assert "# Manifesto" in result

    def test_get_prior_contract_not_found(self):
        result = json.loads(
            execute_scout_tool(
                "get_prior_contract",
                {"contract_type": "phases"},
                _CONTEXT_DATA,
                _PRIOR_CONTRACTS,
            )
        )
        assert "error" in result
        assert "phases" in result["error"]

    def test_submit_contract_returns_error(self):
        """submit_contract is handled by the loop, not the executor."""
        result = json.loads(
            execute_scout_tool(
                "submit_contract",
                {"content": "test"},
                _CONTEXT_DATA,
                _PRIOR_CONTRACTS,
            )
        )
        assert "error" in result

    def test_unknown_tool(self):
        result = json.loads(
            execute_scout_tool("bogus_tool", {}, _CONTEXT_DATA, _PRIOR_CONTRACTS)
        )
        assert "error" in result
        assert "Unknown tool" in result["error"]

    def test_missing_context_returns_empty(self):
        """Empty context_data should return empty structures, not crash."""
        empty = {
            "stack_profile": {},
            "executive_brief": {},
            "migration_tasks": [],
            "dossier": {},
            "checks": [],
            "architecture": {},
        }
        result = json.loads(execute_scout_tool("get_stack_profile", {}, empty, {}))
        assert result == {}


# ===================================================================
# execute_greenfield_tool
# ===================================================================


class TestExecuteGreenfieldTool:
    def test_get_project_intent(self):
        result = json.loads(
            execute_greenfield_tool("get_project_intent", {}, _ANSWERS_DATA, {})
        )
        assert result["product_intent"]["description"] == "A task manager"
        assert result["project_name"] == "TaskFlow"

    def test_get_technical_preferences(self):
        result = json.loads(
            execute_greenfield_tool("get_technical_preferences", {}, _ANSWERS_DATA, {})
        )
        assert result["tech_stack"]["backend"] == "Python"

    def test_get_user_requirements(self):
        result = json.loads(
            execute_greenfield_tool("get_user_requirements", {}, _ANSWERS_DATA, {})
        )
        assert "ui_requirements" in result
        assert "api_endpoints" in result
        assert "database_schema" in result

    def test_get_user_requirements_partial(self):
        """Only includes keys that are present in answers_data."""
        partial = {"ui_requirements": {"theme": "light"}}
        result = json.loads(
            execute_greenfield_tool("get_user_requirements", {}, partial, {})
        )
        assert "ui_requirements" in result
        assert "api_endpoints" not in result

    def test_get_deployment_preferences(self):
        result = json.loads(
            execute_greenfield_tool("get_deployment_preferences", {}, _ANSWERS_DATA, {})
        )
        assert result["platform"] == "AWS"

    def test_get_prior_contract_found(self):
        priors = {"manifesto": "# Manifesto content"}
        result = execute_greenfield_tool(
            "get_prior_contract", {"contract_type": "manifesto"}, _ANSWERS_DATA, priors
        )
        assert "Manifesto content" in result

    def test_get_prior_contract_not_found(self):
        result = json.loads(
            execute_greenfield_tool(
                "get_prior_contract", {"contract_type": "schema"}, _ANSWERS_DATA, {}
            )
        )
        assert "error" in result

    def test_submit_contract_returns_error(self):
        result = json.loads(
            execute_greenfield_tool(
                "submit_contract", {"content": "x"}, _ANSWERS_DATA, {}
            )
        )
        assert "error" in result

    def test_unknown_tool(self):
        result = json.loads(
            execute_greenfield_tool("bogus_tool", {}, _ANSWERS_DATA, {})
        )
        assert "Unknown tool" in result["error"]


# ===================================================================
# _is_placeholder + MINI_DEFAULTS integration
# ===================================================================


class TestIsPlaceholder:
    def test_auto_completed_by_llm(self):
        assert _is_placeholder({"auto": "completed by LLM"}) is True

    def test_auto_inferred(self):
        assert _is_placeholder({"auto": "inferred from conversation"}) is True

    def test_auto_force_completed(self):
        assert _is_placeholder({"auto": "force-completed after 3 exchanges"}) is True

    def test_real_data_not_placeholder(self):
        assert _is_placeholder({"backend": "Python"}) is False

    def test_string_not_placeholder(self):
        assert _is_placeholder("hello") is False

    def test_empty_dict_not_placeholder(self):
        assert _is_placeholder({}) is False

    def test_none_not_placeholder(self):
        assert _is_placeholder(None) is False


class TestGreenfieldMiniDefaults:
    """When questionnaire sections are skipped (placeholder), the executor
    should inject MINI_DEFAULTS so the contract LLM has real data."""

    _MINI_ANSWERS = {
        "product_intent": {"description": "Pomodoro CLI timer"},
        "ui_requirements": {"type": "CLI", "colours": True},
        "tech_stack": {"auto": "completed by LLM"},
        "database_schema": {"auto": "inferred from conversation"},
        "api_endpoints": {"auto": "force-completed after 3 exchanges"},
        "architectural_boundaries": {"auto": "completed by LLM"},
        "deployment_target": {"auto": "completed by LLM"},
        "project_name": "PomoCLI",
        "project_description": "A Pomodoro timer for the terminal",
    }

    def test_tech_prefs_returns_defaults_for_placeholder(self):
        result = json.loads(
            execute_greenfield_tool(
                "get_technical_preferences", {}, self._MINI_ANSWERS, {}
            )
        )
        # Base defaults should be present (adaptive adds _inferred_from_product_intent)
        for k, v in MINI_DEFAULTS["tech_stack"].items():
            assert result["tech_stack"][k] == v
        for k, v in MINI_DEFAULTS["architectural_boundaries"].items():
            assert result["architectural_boundaries"][k] == v
        # Adaptive enrichment should be present since product_intent exists
        assert "_inferred_from_product_intent" in result["tech_stack"]

    def test_user_requirements_returns_defaults_for_placeholder(self):
        result = json.loads(
            execute_greenfield_tool(
                "get_user_requirements", {}, self._MINI_ANSWERS, {}
            )
        )
        # ui_requirements is real data — not replaced
        assert result["ui_requirements"]["type"] == "CLI"
        # api_endpoints and database_schema are placeholders — base defaults present
        for k, v in MINI_DEFAULTS["api_endpoints"].items():
            assert result["api_endpoints"][k] == v
        for k, v in MINI_DEFAULTS["database_schema"].items():
            assert result["database_schema"][k] == v

    def test_deployment_prefs_returns_defaults_for_placeholder(self):
        result = json.loads(
            execute_greenfield_tool(
                "get_deployment_preferences", {}, self._MINI_ANSWERS, {}
            )
        )
        for k, v in MINI_DEFAULTS["deployment_target"].items():
            assert result[k] == v

    def test_project_intent_unaffected_by_placeholders(self):
        """product_intent is always real user data — never defaulted."""
        result = json.loads(
            execute_greenfield_tool(
                "get_project_intent", {}, self._MINI_ANSWERS, {}
            )
        )
        assert result["product_intent"]["description"] == "Pomodoro CLI timer"
        assert result["project_name"] == "PomoCLI"

    def test_real_data_overrides_defaults(self):
        """If user provides real tech_stack, defaults are NOT injected."""
        answers = {**self._MINI_ANSWERS, "tech_stack": {"backend": "Flask"}}
        result = json.loads(
            execute_greenfield_tool(
                "get_technical_preferences", {}, answers, {}
            )
        )
        assert result["tech_stack"]["backend"] == "Flask"
        # architectural_boundaries is still placeholder → base default present
        for k, v in MINI_DEFAULTS["architectural_boundaries"].items():
            assert result["architectural_boundaries"][k] == v

    def test_missing_section_not_defaulted(self):
        """If a section key is entirely missing (not placeholder), no default."""
        sparse = {"product_intent": {"x": 1}, "project_name": "X"}
        result = json.loads(
            execute_greenfield_tool(
                "get_technical_preferences", {}, sparse, {}
            )
        )
        # Key missing entirely — executor skips it, no default injected
        assert result == {}

    def test_deployment_missing_returns_empty(self):
        sparse = {"product_intent": {"x": 1}}
        result = json.loads(
            execute_greenfield_tool(
                "get_deployment_preferences", {}, sparse, {}
            )
        )
        assert result == {}


# ===================================================================
# CONTEXT_TOOLS schema validation
# ===================================================================


class TestToolSchemas:
    def test_scout_tools_have_required_fields(self):
        for tool in CONTEXT_TOOLS_SCOUT:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["input_schema"]["type"] == "object"

    def test_greenfield_tools_have_required_fields(self):
        for tool in CONTEXT_TOOLS_GREENFIELD:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool

    def test_scout_has_submit_contract(self):
        names = {t["name"] for t in CONTEXT_TOOLS_SCOUT}
        assert "submit_contract" in names

    def test_greenfield_has_submit_contract(self):
        names = {t["name"] for t in CONTEXT_TOOLS_GREENFIELD}
        assert "submit_contract" in names

    def test_scout_tool_count(self):
        assert len(CONTEXT_TOOLS_SCOUT) == 8

    def test_greenfield_tool_count(self):
        assert len(CONTEXT_TOOLS_GREENFIELD) == 8

    def test_scout_tool_names(self):
        expected = {
            "get_stack_profile",
            "get_renovation_priorities",
            "get_migration_tasks",
            "get_project_dossier",
            "get_compliance_checks",
            "get_architecture_map",
            "get_prior_contract",
            "submit_contract",
        }
        actual = {t["name"] for t in CONTEXT_TOOLS_SCOUT}
        assert actual == expected

    def test_greenfield_tool_names(self):
        expected = {
            "get_project_intent",
            "get_technical_preferences",
            "get_user_requirements",
            "get_deployment_preferences",
            "get_all_answers",
            "review_draft",
            "get_prior_contract",
            "submit_contract",
        }
        actual = {t["name"] for t in CONTEXT_TOOLS_GREENFIELD}
        assert actual == expected


# ===================================================================
# generate_contract_with_tools — multi-turn loop
# ===================================================================


def _make_tool_use_response(
    tool_calls: list[dict],
    *,
    text: str = "",
    stop_reason: str = "tool_use",
    usage: dict | None = None,
) -> dict:
    """Helper to build an Anthropic-style tool-use response."""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    for tc in tool_calls:
        content.append({
            "type": "tool_use",
            "id": tc.get("id", f"toolu_{uuid4().hex[:12]}"),
            "name": tc["name"],
            "input": tc.get("input", {}),
        })
    return {
        "content": content,
        "stop_reason": stop_reason,
        "usage": usage or {"input_tokens": 100, "output_tokens": 50},
    }


def _make_end_turn_response(text: str, usage: dict | None = None) -> dict:
    """Helper to build an end_turn response with text."""
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": usage or {"input_tokens": 100, "output_tokens": 50},
    }


class TestGenerateContractWithTools:
    """Tests for the multi-turn tool-use generation loop."""

    @pytest.mark.asyncio
    async def test_submit_contract_on_first_turn(self):
        """LLM calls submit_contract immediately → returns content."""
        mock_chat = AsyncMock(
            return_value=_make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Stack\nFastAPI backend"}}],
                stop_reason="tool_use",
            )
        )

        def executor(name, inp):
            return json.dumps({"data": "test"})

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, usage = await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test prompt",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=executor,
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Stack\nFastAPI backend"
        assert usage["input_tokens"] == 100
        assert mock_chat.call_count == 1

    @pytest.mark.asyncio
    async def test_tool_call_then_submit(self):
        """LLM fetches context first, then submits on second turn."""
        responses = [
            # Turn 1: fetch stack
            _make_tool_use_response(
                [{"name": "get_stack_profile", "input": {}}],
                usage={"input_tokens": 200, "output_tokens": 30},
            ),
            # Turn 2: submit contract
            _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Stack\nReact + FastAPI"}}],
                usage={"input_tokens": 300, "output_tokens": 100},
            ),
        ]
        mock_chat = AsyncMock(side_effect=responses)

        call_log = []

        def executor(name, inp):
            call_log.append(name)
            return json.dumps({"backend": {"framework": "FastAPI"}})

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, usage = await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=executor,
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Stack\nReact + FastAPI"
        assert "get_stack_profile" in call_log
        assert usage["input_tokens"] == 500
        assert usage["output_tokens"] == 130
        assert mock_chat.call_count == 2

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_one_turn(self):
        """LLM calls multiple tools in a single turn."""
        responses = [
            # Turn 1: fetch stack + dossier
            _make_tool_use_response(
                [
                    {"name": "get_stack_profile", "input": {}},
                    {"name": "get_project_dossier", "input": {}},
                ],
                usage={"input_tokens": 200, "output_tokens": 50},
            ),
            # Turn 2: submit
            _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Manifesto\nContent here"}}],
                usage={"input_tokens": 400, "output_tokens": 200},
            ),
        ]
        mock_chat = AsyncMock(side_effect=responses)

        call_log = []

        def executor(name, inp):
            call_log.append(name)
            return json.dumps({"data": "ok"})

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, usage = await generate_contract_with_tools(
                contract_type="manifesto",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=executor,
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Manifesto\nContent here"
        assert call_log == ["get_stack_profile", "get_project_dossier"]

    @pytest.mark.asyncio
    async def test_end_turn_fallback(self):
        """If LLM ends turn with text (no submit_contract), use text as fallback."""
        mock_chat = AsyncMock(
            return_value=_make_end_turn_response("# Fallback Blueprint\nContent")
        )

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, usage = await generate_contract_with_tools(
                contract_type="blueprint",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: "{}",
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Fallback Blueprint\nContent"

    @pytest.mark.asyncio
    async def test_end_turn_no_text_raises(self):
        """If LLM ends turn with no text and no submit, raise ValueError."""
        mock_chat = AsyncMock(return_value={
            "content": [],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 0},
        })

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            with pytest.raises(ValueError, match="without submitting"):
                await generate_contract_with_tools(
                    contract_type="blueprint",
                    system_prompt="test",
                    tools=CONTEXT_TOOLS_SCOUT,
                    tool_executor=lambda n, i: "{}",
                    api_key="sk-test",
                    model="claude-sonnet-4-20250514",
                    provider="anthropic",
                )

    @pytest.mark.asyncio
    async def test_max_turns_exceeded(self):
        """Loop should raise ValueError after max_turns without submit."""
        # Always return a tool call that isn't submit_contract
        mock_chat = AsyncMock(
            return_value=_make_tool_use_response(
                [{"name": "get_stack_profile", "input": {}}],
                usage={"input_tokens": 50, "output_tokens": 10},
            )
        )

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            with pytest.raises(ValueError, match="exceeded.*turns"):
                await generate_contract_with_tools(
                    contract_type="stack",
                    system_prompt="test",
                    tools=CONTEXT_TOOLS_SCOUT,
                    tool_executor=lambda n, i: json.dumps({"data": "ok"}),
                    api_key="sk-test",
                    model="claude-sonnet-4-20250514",
                    provider="anthropic",
                    max_turns=3,
                )

        assert mock_chat.call_count == 3

    @pytest.mark.asyncio
    async def test_submit_strips_code_fences(self):
        """Content wrapped in code fences should be stripped."""
        fenced = "```markdown\n# Schema\nTables and relations\n```"
        mock_chat = AsyncMock(
            return_value=_make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": fenced}}]
            )
        )

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, _ = await generate_contract_with_tools(
                contract_type="schema",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: "{}",
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Schema\nTables and relations"

    @pytest.mark.asyncio
    async def test_token_accumulation(self):
        """Usage tokens should accumulate across all turns."""
        responses = [
            _make_tool_use_response(
                [{"name": "get_stack_profile", "input": {}}],
                usage={"input_tokens": 100, "output_tokens": 20},
            ),
            _make_tool_use_response(
                [{"name": "get_project_dossier", "input": {}}],
                usage={"input_tokens": 200, "output_tokens": 30},
            ),
            _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Done"}}],
                usage={"input_tokens": 300, "output_tokens": 400},
            ),
        ]
        mock_chat = AsyncMock(side_effect=responses)

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            _, usage = await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: json.dumps({"data": "ok"}),
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert usage["input_tokens"] == 600
        assert usage["output_tokens"] == 450

    @pytest.mark.asyncio
    async def test_submit_in_multi_tool_response(self):
        """If submit_contract appears alongside other tool calls, it wins."""
        mock_chat = AsyncMock(
            return_value=_make_tool_use_response(
                [
                    {"name": "get_stack_profile", "input": {}},
                    {"name": "submit_contract", "input": {"content": "# Final"}},
                ],
                stop_reason="tool_use",
            )
        )

        call_log = []

        def executor(name, inp):
            call_log.append(name)
            return json.dumps({"data": "ok"})

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, _ = await generate_contract_with_tools(
                contract_type="manifesto",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=executor,
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Final"
        # submit_contract is detected in the first pass over content blocks
        # BEFORE tool execution, so get_stack_profile is never executed
        assert call_log == []

    @pytest.mark.asyncio
    async def test_empty_submit_falls_through(self):
        """submit_contract with empty content is ignored — loop continues."""
        responses = [
            # Turn 1: empty submit — should NOT terminate
            _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": ""}}],
                stop_reason="tool_use",
            ),
            # Turn 2: proper submit
            _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Real content"}}],
                stop_reason="tool_use",
            ),
        ]
        mock_chat = AsyncMock(side_effect=responses)

        with patch("app.services.project.contract_utils.llm_chat", mock_chat):
            content, _ = await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: "{}",
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        assert content == "# Real content"
        assert mock_chat.call_count == 2

    @pytest.mark.asyncio
    async def test_messages_history_built_correctly(self):
        """Verify that the message history is built correctly across turns."""
        call_snapshots = []

        async def capture_chat(**kwargs):
            import copy
            call_snapshots.append(copy.deepcopy(kwargs["messages"]))
            if len(call_snapshots) == 1:
                return _make_tool_use_response(
                    [{"name": "get_stack_profile", "input": {}, "id": "tool_001"}]
                )
            return _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Done"}}]
            )

        with patch("app.services.project.contract_utils.llm_chat", side_effect=capture_chat):
            await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: json.dumps({"result": "stack data"}),
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
            )

        # First call: user message only
        assert len(call_snapshots[0]) == 1
        assert call_snapshots[0][0]["role"] == "user"

        # Second call: user + assistant + tool_result
        msgs = call_snapshots[1]
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        # The tool_result should be in the third message
        assert msgs[2]["content"][0]["type"] == "tool_result"
        assert msgs[2]["content"][0]["tool_use_id"] == "tool_001"

    @pytest.mark.asyncio
    async def test_repo_name_in_initial_message(self):
        """repo_name should appear in the initial user message when provided."""
        call_args = {}

        async def capture_chat(**kwargs):
            call_args.update(kwargs)
            return _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# Done"}}]
            )

        with patch("app.services.project.contract_utils.llm_chat", side_effect=capture_chat):
            await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: "{}",
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                repo_name="user/my-repo",
            )

        user_msg = call_args["messages"][0]["content"]
        assert "user/my-repo" in user_msg

    @pytest.mark.asyncio
    async def test_caching_passed_through(self):
        """enable_caching should be passed to the LLM client."""
        call_kwargs = {}

        async def capture_chat(**kwargs):
            call_kwargs.update(kwargs)
            return _make_tool_use_response(
                [{"name": "submit_contract", "input": {"content": "# X"}}]
            )

        with patch("app.services.project.contract_utils.llm_chat", side_effect=capture_chat):
            await generate_contract_with_tools(
                contract_type="stack",
                system_prompt="test",
                tools=CONTEXT_TOOLS_SCOUT,
                tool_executor=lambda n, i: "{}",
                api_key="sk-test",
                model="claude-sonnet-4-20250514",
                provider="anthropic",
                enable_caching=True,
            )

        assert call_kwargs["enable_caching"] is True


# ===================================================================
# Integration: _extract_context_data (tested via scout_contract_generator)
# ===================================================================


class TestExtractContextData:
    """Test the context extraction helper from scout_contract_generator."""

    def test_extracts_all_fields(self):
        from app.services.project.scout_contract_generator import _extract_context_data

        raw = {
            "stack_profile": _STACK,
            "dossier": _DOSSIER,
            "checks": [{"code": "A0"}],
            "warnings": [{"code": "W1"}],
            "architecture": _ARCH,
            "renovation_plan": {
                "executive_brief": _EXECUTIVE_BRIEF,
                "migration_recommendations": _MIGRATION_TASKS,
            },
        }
        ctx = _extract_context_data(raw)
        assert ctx["stack_profile"] == _STACK
        assert ctx["executive_brief"] == _EXECUTIVE_BRIEF
        assert ctx["migration_tasks"] == _MIGRATION_TASKS
        assert ctx["dossier"] == _DOSSIER
        assert len(ctx["checks"]) == 2  # checks + warnings merged
        assert ctx["architecture"] == _ARCH

    def test_handles_missing_renovation_plan(self):
        from app.services.project.scout_contract_generator import _extract_context_data

        raw = {"stack_profile": _STACK}
        ctx = _extract_context_data(raw)
        assert ctx["executive_brief"] == {}
        assert ctx["migration_tasks"] == []

    def test_handles_empty_input(self):
        from app.services.project.scout_contract_generator import _extract_context_data

        ctx = _extract_context_data({})
        assert ctx["stack_profile"] == {}
        assert ctx["dossier"] == {}
        assert ctx["checks"] == []


# ===================================================================
# Integration: _extract_answers_data (tested via contract_generator)
# ===================================================================


class TestExtractAnswersData:
    """Test the greenfield answer extraction helper."""

    def test_extracts_all_fields(self):
        from app.services.project.contract_generator import _extract_answers_data

        project = {"name": "TaskFlow", "description": "Task management"}
        answers = {
            "product_intent": {"description": "A task manager"},
            "tech_stack": {"backend": "Python"},
            "ui_requirements": {"theme": "dark"},
            "deployment_target": {"platform": "AWS"},
        }
        data = _extract_answers_data(project, answers)
        assert data["product_intent"]["description"] == "A task manager"
        assert data["tech_stack"]["backend"] == "Python"
        assert data["project_name"] == "TaskFlow"
        assert data["project_description"] == "Task management"

    def test_handles_empty_answers(self):
        from app.services.project.contract_generator import _extract_answers_data

        project = {"name": "Empty", "description": ""}
        data = _extract_answers_data(project, {})
        assert data["project_name"] == "Empty"
        assert "tech_stack" not in data

    def test_handles_missing_project_fields(self):
        from app.services.project.contract_generator import _extract_answers_data

        data = _extract_answers_data({}, {"product_intent": {"x": 1}})
        assert data["project_name"] == ""
        assert data["product_intent"] == {"x": 1}
