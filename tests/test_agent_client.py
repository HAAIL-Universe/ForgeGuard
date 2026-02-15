"""Tests for app/clients/agent_client.py -- Agent SDK wrapper."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients import agent_client


# ---------------------------------------------------------------------------
# Tests: query_agent (non-streaming)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_query_agent_success(mock_client_cls):
    """query_agent returns text from the first text content block."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "content": [{"type": "text", "text": "Hello from agent"}]
    }

    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = client_instance

    result = await agent_client.query_agent(
        api_key="test-key",
        model="claude-opus-4-6",
        system_prompt="You are a builder",
        messages=[{"role": "user", "content": "Build something"}],
    )

    assert result == "Hello from agent"
    client_instance.post.assert_called_once()


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_query_agent_empty_content(mock_client_cls):
    """query_agent raises ValueError on empty content."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"content": []}

    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = client_instance

    with pytest.raises(ValueError, match="Empty response"):
        await agent_client.query_agent(
            api_key="test-key",
            model="claude-opus-4-6",
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_query_agent_no_text_block(mock_client_cls):
    """query_agent raises ValueError when no text block found."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "content": [{"type": "image", "source": {}}]
    }

    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = client_instance

    with pytest.raises(ValueError, match="No text block"):
        await agent_client.query_agent(
            api_key="test-key",
            model="claude-opus-4-6",
            system_prompt="test",
            messages=[{"role": "user", "content": "test"}],
        )


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_query_agent_headers(mock_client_cls):
    """query_agent sends correct headers."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "content": [{"type": "text", "text": "ok"}]
    }

    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = client_instance

    await agent_client.query_agent(
        api_key="sk-test-123",
        model="claude-opus-4-6",
        system_prompt="test",
        messages=[{"role": "user", "content": "test"}],
    )

    call_kwargs = client_instance.post.call_args
    headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
    assert headers["x-api-key"] == "sk-test-123"
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_query_agent_max_tokens(mock_client_cls):
    """query_agent passes max_tokens in request body."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {
        "content": [{"type": "text", "text": "ok"}]
    }

    client_instance = AsyncMock()
    client_instance.post.return_value = response
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)
    mock_client_cls.return_value = client_instance

    await agent_client.query_agent(
        api_key="test-key",
        model="claude-opus-4-6",
        system_prompt="test",
        messages=[{"role": "user", "content": "test"}],
        max_tokens=4096,
    )

    call_kwargs = client_instance.post.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert body["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# Tests: stream_agent (streaming, text-only and tool-use)
# ---------------------------------------------------------------------------

def _sse_lines(events: list[dict]) -> list[str]:
    """Convert a list of event dicts into SSE-formatted lines."""
    lines: list[str] = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}")
    lines.append("data: [DONE]")
    return lines


def _make_stream_mock(sse_events: list[dict]):
    """Build a mock httpx.AsyncClient whose .stream() returns SSE lines."""
    lines = _sse_lines(sse_events)

    async def async_iter_lines():
        for line in lines:
            yield line

    response_cm = AsyncMock()
    response_cm.raise_for_status = MagicMock()
    response_cm.aiter_lines = async_iter_lines

    # client.stream() returns an async context manager (not a coroutine)
    stream_cm = MagicMock()
    stream_cm.__aenter__ = AsyncMock(return_value=response_cm)
    stream_cm.__aexit__ = AsyncMock(return_value=False)

    client_instance = MagicMock()
    client_instance.stream.return_value = stream_cm
    client_instance.__aenter__ = AsyncMock(return_value=client_instance)
    client_instance.__aexit__ = AsyncMock(return_value=False)

    return client_instance


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_text_only(mock_client_cls):
    """stream_agent yields text chunks for text-only responses."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}, "model": "claude-opus-4-6"}},
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "delta": {"text": "Hello "}},
        {"type": "content_block_delta", "delta": {"text": "world"}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
    ]
    mock_client_cls.return_value = _make_stream_mock(events)

    usage = agent_client.StreamUsage()
    chunks = []
    async for item in agent_client.stream_agent(
        api_key="key", model="claude-opus-4-6",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        usage_out=usage,
    ):
        chunks.append(item)

    assert all(isinstance(c, str) for c in chunks)
    assert "".join(chunks) == "Hello world"
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
    assert usage.model == "claude-opus-4-6"


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_tool_use(mock_client_cls):
    """stream_agent yields ToolCall for tool_use blocks."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 20}, "model": "claude-opus-4-6"}},
        {"type": "content_block_start", "content_block": {
            "type": "tool_use", "id": "tool_123", "name": "read_file",
        }},
        {"type": "content_block_delta", "delta": {"partial_json": '{"path":'}},
        {"type": "content_block_delta", "delta": {"partial_json": ' "src/main.py"}'}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "usage": {"output_tokens": 15}},
    ]
    mock_client_cls.return_value = _make_stream_mock(events)

    items = []
    async for item in agent_client.stream_agent(
        api_key="key", model="claude-opus-4-6",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "read_file", "description": "read", "input_schema": {"type": "object", "properties": {}, "required": []}}],
    ):
        items.append(item)

    assert len(items) == 1
    tc = items[0]
    assert isinstance(tc, agent_client.ToolCall)
    assert tc.id == "tool_123"
    assert tc.name == "read_file"
    assert tc.input == {"path": "src/main.py"}


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_mixed_text_and_tool(mock_client_cls):
    """stream_agent yields text chunks then ToolCall when response has both."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}, "model": "claude-opus-4-6"}},
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "delta": {"text": "Let me read "}},
        {"type": "content_block_delta", "delta": {"text": "the file."}},
        {"type": "content_block_stop"},
        {"type": "content_block_start", "content_block": {
            "type": "tool_use", "id": "tc_1", "name": "list_directory",
        }},
        {"type": "content_block_delta", "delta": {"partial_json": '{"path": "."}'}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "usage": {"output_tokens": 10}},
    ]
    mock_client_cls.return_value = _make_stream_mock(events)

    items = []
    async for item in agent_client.stream_agent(
        api_key="key", model="claude-opus-4-6",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        tools=[],
    ):
        items.append(item)

    text_parts = [i for i in items if isinstance(i, str)]
    tool_parts = [i for i in items if isinstance(i, agent_client.ToolCall)]
    assert "".join(text_parts) == "Let me read the file."
    assert len(tool_parts) == 1
    assert tool_parts[0].name == "list_directory"
    assert tool_parts[0].input == {"path": "."}


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_tools_in_payload(mock_client_cls):
    """stream_agent includes tools in the API request payload when provided."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0}, "model": "m"}},
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "delta": {"text": "ok"}},
        {"type": "content_block_stop"},
    ]
    mock_client = _make_stream_mock(events)
    mock_client_cls.return_value = mock_client

    test_tools = [{"name": "write_file", "description": "w"}]
    async for _ in agent_client.stream_agent(
        api_key="key", model="m",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        tools=test_tools,
    ):
        pass

    call_kwargs = mock_client.stream.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert body["tools"] == test_tools
    assert body["stream"] is True


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_no_tools_omits_from_payload(mock_client_cls):
    """stream_agent omits tools from payload when None."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0}, "model": "m"}},
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "delta": {"text": "ok"}},
        {"type": "content_block_stop"},
    ]
    mock_client = _make_stream_mock(events)
    mock_client_cls.return_value = mock_client

    async for _ in agent_client.stream_agent(
        api_key="key", model="m",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
    ):
        pass

    call_kwargs = mock_client.stream.call_args
    body = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert "tools" not in body


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_malformed_tool_json(mock_client_cls):
    """stream_agent handles malformed tool input JSON gracefully."""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 0}, "model": "m"}},
        {"type": "content_block_start", "content_block": {
            "type": "tool_use", "id": "tc_bad", "name": "read_file",
        }},
        {"type": "content_block_delta", "delta": {"partial_json": "{invalid_json"}},
        {"type": "content_block_stop"},
    ]
    mock_client_cls.return_value = _make_stream_mock(events)

    items = []
    async for item in agent_client.stream_agent(
        api_key="key", model="m",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        tools=[],
    ):
        items.append(item)

    assert len(items) == 1
    tc = items[0]
    assert isinstance(tc, agent_client.ToolCall)
    assert tc.input == {"_raw": "{invalid_json"}
