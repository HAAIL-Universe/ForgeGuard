"""Tests for forge_ide.agent — the agentic tool-use loop."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from forge_ide.agent import (
    AgentConfig,
    AgentError,
    AgentEvent,
    DoneEvent,
    ErrorEvent,
    TextEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    _UsageAccumulator,
    _format_tool_result,
    run_agent,
    run_task,
    stream_agent,
    DEFAULT_MAX_TURNS,
    MAX_TOOL_RESULT_CHARS,
)
from forge_ide.contracts import ToolResponse
from forge_ide.registry import Registry


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> AgentConfig:
    """AgentConfig with test defaults."""
    defaults = {
        "api_key": "test-key-123",
        "model": "claude-sonnet-4-5-20250514",
        "system_prompt": "You are helpful.",
        "max_turns": 10,
        "max_tokens": 1024,
        "working_dir": "/tmp/test",
        "redact_secrets": False,
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _text_response(text: str, *, input_tokens: int = 10, output_tokens: int = 20) -> dict:
    """Mock an Anthropic API response that returns text with end_turn."""
    return {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "content": [{"type": "text", "text": text}],
    }


def _tool_use_response(
    tool_name: str,
    tool_input: dict,
    *,
    tool_id: str = "toolu_01",
    text: str | None = None,
    input_tokens: int = 15,
    output_tokens: int = 25,
) -> dict:
    """Mock an Anthropic API response that requests a tool call."""
    content = []
    if text:
        content.append({"type": "text", "text": text})
    content.append({
        "type": "tool_use",
        "id": tool_id,
        "name": tool_name,
        "input": tool_input,
    })
    return {
        "stop_reason": "tool_use",
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "content": content,
    }


def _register_echo_tool(registry: Registry) -> None:
    """Register a simple echo tool for testing."""
    from pydantic import BaseModel

    class EchoRequest(BaseModel):
        message: str

    async def echo_handler(req: EchoRequest, working_dir: str) -> ToolResponse:
        return ToolResponse.ok({"echo": req.message})

    registry.register("echo", echo_handler, EchoRequest, "Echo a message back.")


def _register_failing_tool(registry: Registry) -> None:
    """Register a tool that always raises."""
    from pydantic import BaseModel

    class FailRequest(BaseModel):
        reason: str

    async def fail_handler(req: FailRequest, working_dir: str) -> ToolResponse:
        raise RuntimeError(f"Intentional failure: {req.reason}")

    registry.register("fail_tool", fail_handler, FailRequest, "Always fails.")


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig(api_key="k")
        assert cfg.model == "claude-sonnet-4-5-20250514"
        assert cfg.max_turns == DEFAULT_MAX_TURNS
        assert cfg.max_tokens == 16384
        assert cfg.working_dir == "."
        assert cfg.redact_secrets is True
        assert cfg.system_prompt == ""

    def test_frozen(self):
        cfg = AgentConfig(api_key="k")
        with pytest.raises(AttributeError):
            cfg.api_key = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class TestEventTypes:
    def test_text_event(self):
        e = TextEvent(turn=1, elapsed_ms=100, text="hello")
        assert e.text == "hello"
        assert isinstance(e, AgentEvent)

    def test_tool_call_event(self):
        e = ToolCallEvent(turn=2, elapsed_ms=200, tool_name="read", tool_input={"path": "x"}, tool_use_id="t1")
        assert e.tool_name == "read"
        assert e.tool_use_id == "t1"

    def test_tool_result_event(self):
        resp = ToolResponse.ok({"content": "data"})
        e = ToolResultEvent(turn=2, elapsed_ms=300, tool_name="read", tool_use_id="t1", response=resp)
        assert e.response.success

    def test_done_event(self):
        e = DoneEvent(turn=3, elapsed_ms=500, final_text="Done!", total_input_tokens=100, total_output_tokens=50, tool_calls_made=2)
        assert e.final_text == "Done!"
        assert e.tool_calls_made == 2

    def test_error_event(self):
        e = ErrorEvent(turn=1, elapsed_ms=50, error="boom")
        assert e.error == "boom"

    def test_thinking_event(self):
        e = ThinkingEvent(turn=1, elapsed_ms=10, text="Let me think...")
        assert e.text == "Let me think..."


# ---------------------------------------------------------------------------
# UsageAccumulator
# ---------------------------------------------------------------------------


class TestUsageAccumulator:
    def test_add(self):
        u = _UsageAccumulator()
        u.add({"input_tokens": 10, "output_tokens": 5})
        u.add({"input_tokens": 20, "output_tokens": 15})
        assert u.input_tokens == 30
        assert u.output_tokens == 20

    def test_add_missing_keys(self):
        u = _UsageAccumulator()
        u.add({})
        assert u.input_tokens == 0
        assert u.output_tokens == 0

    def test_tool_calls_tracking(self):
        u = _UsageAccumulator()
        assert u.tool_calls == 0
        u.tool_calls += 1
        assert u.tool_calls == 1


# ---------------------------------------------------------------------------
# _format_tool_result
# ---------------------------------------------------------------------------


class TestFormatToolResult:
    def test_success(self):
        result = ToolResponse.ok({"greeting": "hello"})
        text = _format_tool_result(result, redact_secrets=False)
        parsed = json.loads(text)
        assert parsed["greeting"] == "hello"

    def test_error(self):
        result = ToolResponse.fail("something broke")
        text = _format_tool_result(result, redact_secrets=False)
        assert text.startswith("ERROR:")
        assert "something broke" in text

    def test_truncation(self):
        # Create a large result
        big_data = {"content": "x" * (MAX_TOOL_RESULT_CHARS + 1000)}
        result = ToolResponse.ok(big_data)
        text = _format_tool_result(result, redact_secrets=False)
        assert len(text) < MAX_TOOL_RESULT_CHARS + 200  # room for suffix
        assert "truncated" in text

    def test_redaction(self):
        # Embed something that looks like a secret
        result = ToolResponse.ok({"key": "AKIAIOSFODNN7EXAMPLE"})
        text = _format_tool_result(result, redact_secrets=True)
        # The AWS key pattern should be redacted
        assert "AKIAIOSFODNN7EXAMPLE" not in text


# ---------------------------------------------------------------------------
# run_agent — single-turn (no tool use)
# ---------------------------------------------------------------------------


class TestRunAgentSingleTurn:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_simple_text_response(self, mock_chat):
        mock_chat.return_value = _text_response("Hello!")
        registry = Registry()
        config = _make_config()

        done = await run_agent("Say hello", registry, config)

        assert isinstance(done, DoneEvent)
        assert done.final_text == "Hello!"
        assert done.turn == 1
        assert done.total_input_tokens == 10
        assert done.total_output_tokens == 20
        assert done.tool_calls_made == 0

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_events_emitted(self, mock_chat):
        mock_chat.return_value = _text_response("Hello!")
        registry = Registry()
        config = _make_config()
        events: list[AgentEvent] = []

        done = await run_agent("Say hello", registry, config, on_event=events.append)

        # Should emit TextEvent, then DoneEvent
        assert len(events) == 2
        assert isinstance(events[0], TextEvent)
        assert events[0].text == "Hello!"
        assert isinstance(events[1], DoneEvent)

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_async_event_callback(self, mock_chat):
        mock_chat.return_value = _text_response("Hi!")
        registry = Registry()
        config = _make_config()
        events: list[AgentEvent] = []

        async def async_cb(event: AgentEvent):
            events.append(event)

        done = await run_agent("Say hi", registry, config, on_event=async_cb)

        assert len(events) == 2
        assert done.final_text == "Hi!"


# ---------------------------------------------------------------------------
# run_agent — multi-turn with tool calls
# ---------------------------------------------------------------------------


class TestRunAgentToolUse:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_tool_call_then_final(self, mock_chat):
        """LLM calls echo tool, gets result, then produces final text."""
        mock_chat.side_effect = [
            _tool_use_response("echo", {"message": "ping"}, tool_id="t_001"),
            _text_response("The echo said: ping"),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()
        events: list[AgentEvent] = []

        done = await run_agent("Echo ping", registry, config, on_event=events.append)

        assert done.final_text == "The echo said: ping"
        assert done.tool_calls_made == 1
        assert done.turn == 2

        # Verify event sequence
        event_types = [type(e).__name__ for e in events]
        assert "ToolCallEvent" in event_types
        assert "ToolResultEvent" in event_types
        assert event_types[-1] == "DoneEvent"

        # Verify the messages sent to the LLM
        assert mock_chat.call_count == 2
        # Second call should include tool results
        second_call = mock_chat.call_args_list[1]
        messages = second_call.kwargs["messages"]
        # messages = [user, assistant, user(tool_result)]
        tool_result_msg = [m for m in messages if m["role"] == "user" and isinstance(m.get("content"), list)][-1]
        assert any(
            item.get("type") == "tool_result"
            for item in tool_result_msg["content"]
        )

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_multiple_tool_calls_in_one_turn(self, mock_chat):
        """LLM requests two tool calls in a single response."""
        # Response with two tool_use blocks
        multi_tool_response = {
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 20, "output_tokens": 30},
            "content": [
                {"type": "tool_use", "id": "t_01", "name": "echo", "input": {"message": "one"}},
                {"type": "tool_use", "id": "t_02", "name": "echo", "input": {"message": "two"}},
            ],
        }
        mock_chat.side_effect = [
            multi_tool_response,
            _text_response("Got both echos"),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()

        done = await run_agent("Echo both", registry, config)

        assert done.tool_calls_made == 2
        assert done.final_text == "Got both echos"

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_tool_with_preceding_text(self, mock_chat):
        """LLM emits text BEFORE the tool_use block."""
        mock_chat.side_effect = [
            _tool_use_response("echo", {"message": "test"}, text="Let me echo that..."),
            _text_response("Done echoing"),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()
        events: list[AgentEvent] = []

        done = await run_agent("Echo test", registry, config, on_event=events.append)

        text_events = [e for e in events if isinstance(e, TextEvent)]
        assert any("Let me echo" in e.text for e in text_events)

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_tool_exception_handled(self, mock_chat):
        """When a tool raises, we send an error result back (not crash)."""
        mock_chat.side_effect = [
            _tool_use_response("fail_tool", {"reason": "testing"}, tool_id="t_fail"),
            _text_response("I see the tool failed."),
        ]
        registry = Registry()
        _register_failing_tool(registry)
        config = _make_config()

        done = await run_agent("Try failing", registry, config)

        assert done.final_text == "I see the tool failed."
        assert done.tool_calls_made == 1

        # Verify the error was sent back as tool_result
        second_call_msgs = mock_chat.call_args_list[1].kwargs["messages"]
        tool_result_msgs = [m for m in second_call_msgs if m["role"] == "user" and isinstance(m.get("content"), list)]
        tool_result_msg = tool_result_msgs[-1]
        result_text = tool_result_msg["content"][0]["content"]
        assert "ERROR:" in result_text or "exception" in result_text.lower()


# ---------------------------------------------------------------------------
# run_agent — error handling
# ---------------------------------------------------------------------------


class TestRunAgentErrors:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_api_error_raises_agent_error(self, mock_chat):
        mock_chat.side_effect = ValueError("API 500: Internal Server Error")
        registry = Registry()
        config = _make_config()
        events: list[AgentEvent] = []

        with pytest.raises(AgentError, match="API 500"):
            await run_agent("hello", registry, config, on_event=events.append)

        # ErrorEvent should have been emitted
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) == 1
        assert "API 500" in error_events[0].error

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_max_turns_exhaustion(self, mock_chat):
        """When max turns is reached, the loop returns gracefully."""
        # Always return tool_use — never end_turn
        mock_chat.return_value = _tool_use_response("echo", {"message": "loop"})
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config(max_turns=3)
        events: list[AgentEvent] = []

        done = await run_agent("infinite loop", registry, config, on_event=events.append)

        assert isinstance(done, DoneEvent)
        assert done.turn == 3
        assert done.tool_calls_made == 3

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_no_content_blocks(self, mock_chat):
        """Response with no content blocks but end_turn returns empty text."""
        mock_chat.return_value = {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 5, "output_tokens": 5},
            "content": [],
        }
        registry = Registry()
        config = _make_config()

        done = await run_agent("empty response", registry, config)

        assert done.final_text == ""


# ---------------------------------------------------------------------------
# run_agent — usage tracking
# ---------------------------------------------------------------------------


class TestRunAgentUsage:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_usage_accumulated_across_turns(self, mock_chat):
        mock_chat.side_effect = [
            _tool_use_response("echo", {"message": "a"}, input_tokens=10, output_tokens=20),
            _tool_use_response("echo", {"message": "b"}, input_tokens=15, output_tokens=25),
            _text_response("done", input_tokens=5, output_tokens=10),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()

        done = await run_agent("multi step", registry, config)

        assert done.total_input_tokens == 30   # 10 + 15 + 5
        assert done.total_output_tokens == 55  # 20 + 25 + 10
        assert done.tool_calls_made == 2


# ---------------------------------------------------------------------------
# run_agent — message construction
# ---------------------------------------------------------------------------


class TestRunAgentMessages:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_initial_message_structure(self, mock_chat):
        """First call sends the task as a user message."""
        mock_chat.return_value = _text_response("ok")
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()

        await run_agent("Do the thing", registry, config)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs["api_key"] == "test-key-123"
        assert call_kwargs["model"] == "claude-sonnet-4-5-20250514"
        assert call_kwargs["system_prompt"] == "You are helpful."
        messages = call_kwargs["messages"]
        assert messages[0] == {"role": "user", "content": "Do the thing"}
        # tools should be passed
        assert "tools" in call_kwargs
        assert isinstance(call_kwargs["tools"], list)

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_assistant_messages_appended(self, mock_chat):
        """After a tool call, assistant message with content blocks is appended."""
        original_content = [
            {"type": "text", "text": "I'll echo that"},
            {"type": "tool_use", "id": "t_1", "name": "echo", "input": {"message": "x"}},
        ]
        mock_chat.side_effect = [
            {
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "content": original_content,
            },
            _text_response("finished"),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()

        await run_agent("echo x", registry, config)

        # Second call's messages should contain assistant + user (tool_result)
        second_call_msgs = mock_chat.call_args_list[1].kwargs["messages"]
        # messages[0] = original user, messages[1] = assistant, messages[2] = tool_result
        assert second_call_msgs[1]["role"] == "assistant"
        assert second_call_msgs[1]["content"] == original_content
        assert second_call_msgs[2]["role"] == "user"

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_system_prompt_passed(self, mock_chat):
        """The system prompt from config is forwarded to the LLM."""
        mock_chat.return_value = _text_response("ok")
        config = _make_config(system_prompt="Be concise.")

        await run_agent("hi", Registry(), config)

        assert mock_chat.call_args.kwargs["system_prompt"] == "Be concise."


# ---------------------------------------------------------------------------
# stream_agent
# ---------------------------------------------------------------------------


class TestStreamAgent:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_stream_yields_events(self, mock_chat):
        mock_chat.side_effect = [
            _tool_use_response("echo", {"message": "ping"}, tool_id="t_1"),
            _text_response("pong"),
        ]
        registry = Registry()
        _register_echo_tool(registry)
        config = _make_config()

        events = []
        async for event in stream_agent("stream test", registry, config):
            events.append(event)

        assert len(events) > 0
        assert isinstance(events[-1], DoneEvent)
        event_types = {type(e).__name__ for e in events}
        assert "ToolCallEvent" in event_types
        assert "ToolResultEvent" in event_types
        assert "TextEvent" in event_types

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_stream_simple(self, mock_chat):
        mock_chat.return_value = _text_response("hello stream")
        registry = Registry()
        config = _make_config()

        events = []
        async for event in stream_agent("simple", registry, config):
            events.append(event)

        assert any(isinstance(e, DoneEvent) for e in events)
        done = [e for e in events if isinstance(e, DoneEvent)][0]
        assert done.final_text == "hello stream"

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_stream_error_yields_error_event(self, mock_chat):
        mock_chat.side_effect = ValueError("stream boom")
        registry = Registry()
        config = _make_config()

        events = []
        async for event in stream_agent("fail", registry, config):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) >= 1


# ---------------------------------------------------------------------------
# run_task — high-level convenience
# ---------------------------------------------------------------------------


class TestRunTask:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_run_task_creates_registry(self, mock_chat):
        """run_task auto-creates a Registry with builtin tools."""
        mock_chat.return_value = _text_response("task done")

        done = await run_task(
            "Do something",
            api_key="test-key",
            working_dir="/tmp/test",
        )

        assert isinstance(done, DoneEvent)
        assert done.final_text == "task done"
        # The call should have tools registered
        call_kwargs = mock_chat.call_args.kwargs
        tools = call_kwargs["tools"]
        assert isinstance(tools, list)
        assert len(tools) > 0  # builtin tools should be registered
        tool_names = [t["name"] for t in tools]
        assert "read_file" in tool_names
        assert "search_code" in tool_names

    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_run_task_custom_model(self, mock_chat):
        mock_chat.return_value = _text_response("ok")

        await run_task(
            "Custom model test",
            api_key="k",
            working_dir=".",
            model="claude-opus-4-20250514",
        )

        assert mock_chat.call_args.kwargs["model"] == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# Redaction in tool results
# ---------------------------------------------------------------------------


class TestRedactionIntegration:
    @pytest.mark.asyncio
    @patch("forge_ide.agent.chat_anthropic")
    async def test_secrets_redacted_when_enabled(self, mock_chat):
        """Tool results containing secrets are redacted before sending to LLM."""

        # Custom tool that returns a fake AWS key
        registry = Registry()

        from pydantic import BaseModel

        class SecretRequest(BaseModel):
            query: str

        async def secret_handler(req, wd):
            return ToolResponse.ok({"key": "AKIAIOSFODNN7EXAMPLE"})

        registry.register("leak_secrets", secret_handler, SecretRequest, "Returns secrets")

        mock_chat.side_effect = [
            _tool_use_response("leak_secrets", {"query": "gimme"}, tool_id="s1"),
            _text_response("Got it"),
        ]
        config = _make_config(redact_secrets=True)

        await run_agent("get secrets", registry, config)

        # Check what was sent as tool_result
        second_call = mock_chat.call_args_list[1]
        msgs = second_call.kwargs["messages"]
        tool_result_msgs = [m for m in msgs if m["role"] == "user" and isinstance(m.get("content"), list)]
        tool_result_msg = tool_result_msgs[-1]
        result_text = tool_result_msg["content"][0]["content"]
        assert "AKIAIOSFODNN7EXAMPLE" not in result_text


# ---------------------------------------------------------------------------
# llm_client.chat_anthropic — tool_use support
# ---------------------------------------------------------------------------


class TestChatAnthropicToolUse:
    """Tests for the tools parameter added to chat_anthropic."""

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.httpx.AsyncClient")
    async def test_tools_in_request_body(self, mock_client_cls):
        """When tools are provided, they appear in the request body."""
        from unittest.mock import MagicMock
        from app.clients.llm_client import chat_anthropic

        response_data = {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "content": [{"type": "text", "text": "ok"}],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        tools = [{"name": "echo", "description": "Echo", "input_schema": {"type": "object", "properties": {}}}]
        result = await chat_anthropic(
            api_key="k",
            model="claude-sonnet-4-5-20250514",
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
        )

        # With tools, the full response is returned
        assert result == response_data

        # Verify tools were in the request body
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1]["json"]
        assert "tools" in body
        assert body["tools"] == tools

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.httpx.AsyncClient")
    async def test_no_tools_returns_simplified(self, mock_client_cls):
        """Without tools, the simplified dict is returned."""
        from unittest.mock import MagicMock
        from app.clients.llm_client import chat_anthropic

        response_data = {
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "content": [{"type": "text", "text": "hello"}],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await chat_anthropic(
            api_key="k",
            model="claude-sonnet-4-5-20250514",
            system_prompt="sys",
            messages=[{"role": "user", "content": "hi"}],
        )

        # Without tools, simplified dict
        assert result == {"text": "hello", "usage": {"input_tokens": 10, "output_tokens": 20}}

        # No tools in body
        call_kwargs = mock_client.post.call_args
        body = call_kwargs.kwargs.get("json") or call_kwargs[1]["json"]
        assert "tools" not in body

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.httpx.AsyncClient")
    async def test_tool_use_response_returned_fully(self, mock_client_cls):
        """When LLM returns tool_use blocks, the full response is passed through."""
        from unittest.mock import MagicMock
        from app.clients.llm_client import chat_anthropic

        response_data = {
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 15, "output_tokens": 30},
            "content": [
                {"type": "text", "text": "I'll read that file"},
                {"type": "tool_use", "id": "tu_1", "name": "read_file", "input": {"path": "foo.py"}},
            ],
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        tools = [{"name": "read_file", "description": "Read", "input_schema": {"type": "object"}}]
        result = await chat_anthropic(
            api_key="k",
            model="m",
            system_prompt="s",
            messages=[{"role": "user", "content": "read foo.py"}],
            tools=tools,
        )

        assert result["stop_reason"] == "tool_use"
        assert len(result["content"]) == 2
        assert result["content"][1]["type"] == "tool_use"
        assert result["content"][1]["name"] == "read_file"


# ---------------------------------------------------------------------------
# chat() unified entry point — tools passthrough
# ---------------------------------------------------------------------------


class TestChatUnifiedTools:
    @pytest.mark.asyncio
    @patch("app.clients.llm_client.chat_anthropic")
    async def test_chat_passes_tools_to_anthropic(self, mock_chat_anthropic):
        from app.clients.llm_client import chat

        mock_chat_anthropic.return_value = {"stop_reason": "end_turn", "content": []}
        tools = [{"name": "test_tool"}]

        await chat(
            api_key="k",
            model="m",
            system_prompt="s",
            messages=[],
            provider="anthropic",
            tools=tools,
        )

        mock_chat_anthropic.assert_called_once()
        assert mock_chat_anthropic.call_args.kwargs.get("tools") == tools

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.chat_openai")
    async def test_chat_openai_ignores_tools(self, mock_chat_openai):
        """OpenAI path doesn't receive tools parameter."""
        from app.clients.llm_client import chat

        mock_chat_openai.return_value = {"text": "hi", "usage": {}}

        await chat(
            api_key="k",
            model="m",
            system_prompt="s",
            messages=[],
            provider="openai",
            tools=[{"name": "ignored"}],
        )

        mock_chat_openai.assert_called_once()
        # OpenAI function doesn't accept tools, so it shouldn't be in kwargs
        call_kwargs = mock_chat_openai.call_args
        # The call should work (no TypeError) — tools are not forwarded
        assert "tools" not in (call_kwargs.kwargs or {})


# ---------------------------------------------------------------------------
# __init__.py exports
# ---------------------------------------------------------------------------


class TestInitExports:
    def test_agent_exports_available(self):
        """Agent loop types are importable from forge_ide top-level."""
        from forge_ide import (
            AgentConfig,
            AgentError,
            AgentEvent,
            DoneEvent,
            ErrorEvent,
            TextEvent,
            ThinkingEvent,
            ToolCallEvent,
            ToolResultEvent,
            run_agent,
            run_task,
            stream_agent,
        )

        assert AgentConfig is not None
        assert callable(run_agent)
        assert callable(stream_agent)
        assert callable(run_task)
