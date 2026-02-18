"""Tests for the tiered auto-fix system.

Covers:
- _parse_test_failures  — structured extraction from pytest output
- _read_failing_files   — file reading with budget
- _auto_fix_loop        — tiered escalation (mocked LLM calls)
- chat_anthropic        — thinking_budget parameter wiring
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import the functions under test
# ---------------------------------------------------------------------------
from app.services.upgrade_executor import (
    _parse_test_failures,
    _read_failing_files,
    _extract_json_bracket,
    _safe_json_parse,
)


# ═══════════════════════════════════════════════════════════════════════════
# _parse_test_failures
# ═══════════════════════════════════════════════════════════════════════════


class TestParseTestFailures:
    """Verify structured failure extraction from pytest output."""

    def test_parses_failed_line_with_error(self):
        output = "FAILED tests/test_foo.py::test_bar - SyntaxError: invalid syntax"
        result = _parse_test_failures(output)
        assert len(result) == 1
        assert result[0]["file"] == "tests/test_foo.py"
        assert result[0]["test"] == "test_bar"
        assert result[0]["error_type"] == "SyntaxError"
        assert "invalid syntax" in result[0]["message"]

    def test_parses_failed_line_without_error_detail(self):
        output = "FAILED tests/test_auth.py::test_login"
        result = _parse_test_failures(output)
        assert len(result) == 1
        assert result[0]["file"] == "tests/test_auth.py"
        assert result[0]["test"] == "test_login"
        assert result[0]["error_type"] == "Unknown"

    def test_parses_multiple_failures(self):
        output = textwrap.dedent("""\
            FAILED tests/test_a.py::test_one - AssertionError: 1 != 2
            FAILED tests/test_b.py::test_two - TypeError: bad type
            2 failed, 5 passed
        """)
        result = _parse_test_failures(output)
        assert len(result) == 2
        # After severity sort: TypeError (4) < AssertionError (99)
        assert result[0]["file"] == "tests/test_b.py"
        assert result[1]["file"] == "tests/test_a.py"

    def test_parses_traceback_style(self):
        output = textwrap.dedent("""\
            tests/__init__.py:1: in <module>
            E   SyntaxError: invalid syntax
        """)
        result = _parse_test_failures(output)
        # Should pick up the file from traceback
        assert any(f["file"] == "tests/__init__.py" for f in result)

    def test_empty_output(self):
        assert _parse_test_failures("") == []

    def test_all_passing_output(self):
        output = "5 passed in 2.30s"
        assert _parse_test_failures(output) == []

    def test_deduplicates_by_file(self):
        """Traceback matches should not duplicate files already captured."""
        output = textwrap.dedent("""\
            FAILED tests/test_foo.py::test_bar - SyntaxError: bad
            tests/test_foo.py:10: in test_bar
        """)
        result = _parse_test_failures(output)
        files = [f["file"] for f in result]
        assert files.count("tests/test_foo.py") == 1


# ═══════════════════════════════════════════════════════════════════════════
# _read_failing_files
# ═══════════════════════════════════════════════════════════════════════════


class TestReadFailingFiles:
    """Verify file reading from failures + changes."""

    def test_reads_existing_files(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").write_text("def test(): pass\n")
        (tmp_path / "app.py").write_text("print('hi')\n")

        failures = [{"file": "tests/test_foo.py"}]
        changes = [{"file": "app.py"}]

        result = _read_failing_files(str(tmp_path), failures, changes)
        assert "tests/test_foo.py" in result
        assert "app.py" in result

    def test_skips_missing_files(self, tmp_path):
        failures = [{"file": "nonexistent.py"}]
        result = _read_failing_files(str(tmp_path), failures, [])
        assert result == {}

    def test_budget_truncation(self, tmp_path):
        big_content = "x" * 300_000
        (tmp_path / "big.py").write_text(big_content)
        (tmp_path / "small.py").write_text("ok\n")

        failures = [{"file": "big.py"}, {"file": "small.py"}]
        result = _read_failing_files(str(tmp_path), failures, [])

        # big.py should be truncated, small.py may or may not fit
        assert "big.py" in result
        assert "[TRUNCATED]" in result["big.py"]


# ═══════════════════════════════════════════════════════════════════════════
# chat_anthropic — thinking_budget wiring
# ═══════════════════════════════════════════════════════════════════════════


class TestChatAnthropicThinking:
    """Verify that thinking_budget is wired correctly into the API body."""

    @pytest.mark.asyncio
    async def test_thinking_budget_adds_thinking_param(self):
        """When thinking_budget > 0, the body should include thinking config."""
        captured_body = {}

        async def mock_post(url, headers, json):
            captured_body.update(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "content": [
                    {"type": "thinking", "thinking": "Let me reason..."},
                    {"type": "text", "text": '{"fix": true}'},
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post

        with patch("app.clients.llm_client._get_client", return_value=mock_client):
            from app.clients.llm_client import chat_anthropic

            result = await chat_anthropic(
                api_key="test-key",
                model="claude-sonnet-4-5",
                system_prompt="You are a fixer.",
                messages=[{"role": "user", "content": "Fix this"}],
                max_tokens=4096,
                thinking_budget=10000,
            )

        assert "thinking" in captured_body
        assert captured_body["thinking"]["type"] == "enabled"
        assert captured_body["thinking"]["budget_tokens"] == 10000
        # max_tokens should be at least thinking_budget + 4096
        assert captured_body["max_tokens"] >= 10000 + 4096
        # Result should contain thinking text
        assert "thinking" in result
        assert "Let me reason" in result["thinking"]
        assert result["text"] == '{"fix": true}'

    @pytest.mark.asyncio
    async def test_no_thinking_budget_omits_param(self):
        """When thinking_budget is 0, no thinking config should be sent."""
        captured_body = {}

        async def mock_post(url, headers, json):
            captured_body.update(json)
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "content": [{"type": "text", "text": "hello"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post

        with patch("app.clients.llm_client._get_client", return_value=mock_client):
            from app.clients.llm_client import chat_anthropic

            await chat_anthropic(
                api_key="test-key",
                model="claude-sonnet-4-5",
                system_prompt="Hello",
                messages=[{"role": "user", "content": "Hi"}],
                thinking_budget=0,
            )

        assert "thinking" not in captured_body


# ═══════════════════════════════════════════════════════════════════════════
# _auto_fix_loop integration (mocked LLM)
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoFixLoop:
    """Integration tests for the tiered auto-fix loop with mocked LLM."""

    @pytest.mark.asyncio
    async def test_tier1_succeeds_on_first_attempt(self, tmp_path):
        """Fix loop should return (True, output) when Tier 1 fixes tests."""
        from app.services.upgrade_executor import _auto_fix_loop

        # Create a working file
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "__init__.py").write_text("")

        state = {
            "api_key": "test-key",
            "planner_model": "claude-sonnet-4-5",
            "builder_model": "claude-opus-4-6",
            "working_dir": str(tmp_path),
        }
        all_changes = [{"file": "tests/__init__.py", "action": "modify"}]
        test_output = "FAILED tests/__init__.py::test_x - SyntaxError: invalid"

        plan_response = {
            "text": json.dumps({
                "diagnosis": "tests/__init__.py has invalid syntax",
                "plan": [{"file": "tests/__init__.py", "action": "modify",
                          "description": "Fix syntax"}],
            }),
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        build_response = {
            "text": json.dumps({
                "changes": [{
                    "file": "tests/__init__.py",
                    "action": "modify",
                    "description": "Fix syntax",
                    "after_snippet": "",
                }],
            }),
            "usage": {"input_tokens": 200, "output_tokens": 100},
        }

        call_count = 0

        async def mock_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return plan_response  # Sonnet diagnosis
            return build_response  # Opus fix

        # _run_tests should pass after fix
        async def mock_run_tests(uid, rid, st):
            return True, "1 passed"

        with patch("app.services.upgrade_executor.chat", side_effect=mock_chat), \
             patch("app.services.upgrade_executor._run_tests", side_effect=mock_run_tests), \
             patch("app.services.upgrade_executor._log", new_callable=AsyncMock), \
             patch("app.services.upgrade_executor._emit", new_callable=AsyncMock), \
             patch("app.services.upgrade_executor.settings") as mock_settings:
            mock_settings.LLM_FIX_MAX_TIER1 = 3
            mock_settings.LLM_FIX_MAX_TIER2 = 3
            mock_settings.LLM_THINKING_BUDGET = 10000
            mock_settings.LLM_BUILDER_MAX_TOKENS = 16384
            mock_settings.LLM_PLANNER_MODEL = "claude-sonnet-4-5"
            mock_settings.LLM_BUILDER_MODEL = "claude-opus-4-6"

            passed, output = await _auto_fix_loop(
                "user1", "run1", state, all_changes, test_output)

        assert passed is True
        assert output == "1 passed"

    @pytest.mark.asyncio
    async def test_all_tiers_exhausted(self, tmp_path):
        """Fix loop should return (False, output) when all attempts fail."""
        from app.services.upgrade_executor import _auto_fix_loop

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "__init__.py").write_text("bad")

        state = {
            "api_key": "test-key",
            "planner_model": "claude-sonnet-4-5",
            "builder_model": "claude-opus-4-6",
            "working_dir": str(tmp_path),
        }

        plan_response = {
            "text": json.dumps({
                "diagnosis": "needs fix",
                "plan": [{"file": "tests/__init__.py", "action": "modify",
                          "description": "attempt fix"}],
            }),
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }
        build_response = {
            "text": json.dumps({
                "changes": [{
                    "file": "tests/__init__.py",
                    "action": "modify",
                    "description": "try fix",
                    "after_snippet": "still bad",
                }],
            }),
            "usage": {"input_tokens": 200, "output_tokens": 100},
        }

        async def mock_chat(**kwargs):
            if kwargs.get("thinking_budget", 0) > 0:
                return plan_response  # Tier 2
            return plan_response if "Fix Planner" in kwargs.get("system_prompt", "") else build_response

        async def mock_run_tests(uid, rid, st):
            return False, "FAILED tests/__init__.py"

        with patch("app.services.upgrade_executor.chat", side_effect=mock_chat), \
             patch("app.services.upgrade_executor._run_tests", side_effect=mock_run_tests), \
             patch("app.services.upgrade_executor._log", new_callable=AsyncMock), \
             patch("app.services.upgrade_executor._emit", new_callable=AsyncMock), \
             patch("app.services.upgrade_executor.settings") as mock_settings:
            mock_settings.LLM_FIX_MAX_TIER1 = 2
            mock_settings.LLM_FIX_MAX_TIER2 = 2
            mock_settings.LLM_THINKING_BUDGET = 10000
            mock_settings.LLM_BUILDER_MAX_TOKENS = 16384

            passed, output = await _auto_fix_loop(
                "user1", "run1", state, [],
                "FAILED tests/__init__.py")

        assert passed is False


# ═══════════════════════════════════════════════════════════════════════════
# Config settings
# ═══════════════════════════════════════════════════════════════════════════


class TestFixConfig:
    """Verify default config values for fix loop."""

    def test_default_tier_settings(self):
        from app.config import Settings
        s = Settings()
        assert s.LLM_FIX_MAX_TIER1 == 3
        assert s.LLM_FIX_MAX_TIER2 == 3


# ═══════════════════════════════════════════════════════════════════════════
# _extract_json_bracket / _safe_json_parse
# ═══════════════════════════════════════════════════════════════════════════


class TestExtractJsonBracket:
    """Verify bracket-counting JSON extraction."""

    def test_simple_object(self):
        assert _extract_json_bracket('{"a": 1}') == '{"a": 1}'

    def test_extra_data_after_object(self):
        """The 'Extra data' scenario that was wasting fix attempts."""
        text = '{"changes": []}{"extra": "junk"}'
        result = _extract_json_bracket(text)
        assert result == '{"changes": []}'
        assert json.loads(result) == {"changes": []}

    def test_prose_before_json(self):
        text = "Here is the fix:\n{\"changes\": [{\"file\": \"x.py\"}]}"
        result = _extract_json_bracket(text)
        assert json.loads(result) == {"changes": [{"file": "x.py"}]}

    def test_nested_braces(self):
        text = '{"a": {"b": {"c": 1}}}'
        assert _extract_json_bracket(text) == text

    def test_string_with_braces(self):
        text = '{"msg": "hello { world }"}'
        assert _extract_json_bracket(text) == text
        assert json.loads(_extract_json_bracket(text)) == {"msg": "hello { world }"}

    def test_no_json(self):
        assert _extract_json_bracket("no json here") is None

    def test_array(self):
        text = '[1, 2, 3]'
        assert _extract_json_bracket(text) == text


class TestSafeJsonParse:
    """Verify multi-strategy JSON parsing."""

    def test_clean_json(self):
        assert _safe_json_parse('{"a": 1}') == {"a": 1}

    def test_codeblock_wrapped(self):
        text = '```json\n{"a": 1}\n```'
        assert _safe_json_parse(text) == {"a": 1}

    def test_extra_data(self):
        text = '{"changes": []}{"extra": true}'
        result = _safe_json_parse(text)
        assert result == {"changes": []}

    def test_prose_preamble(self):
        text = "I'll fix the issue now.\n{\"changes\": []}"
        assert _safe_json_parse(text) == {"changes": []}

    def test_returns_none_for_garbage(self):
        assert _safe_json_parse("not json at all") is None

    def test_empty_string(self):
        assert _safe_json_parse("") is None


class TestFailureSeveritySorting:
    """Verify that blocking errors sort first."""

    def test_import_error_sorts_first(self):
        output = textwrap.dedent("""\
            FAILED tests/test_a.py::test_one - AssertionError: 1 != 2
            FAILED tests/test_b.py::test_two - ImportError: No module
        """)
        result = _parse_test_failures(output)
        assert result[0]["error_type"] == "ImportError"
        assert result[1]["error_type"] == "AssertionError"

    def test_syntax_before_assertion(self):
        output = textwrap.dedent("""\
            FAILED tests/test_a.py::test_one - RuntimeError: failed
            FAILED tests/test_b.py::test_two - SyntaxError: bad
        """)
        result = _parse_test_failures(output)
        assert result[0]["error_type"] == "SyntaxError"
