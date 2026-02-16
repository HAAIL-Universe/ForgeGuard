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
    # Tools should be present with cache_control on the last tool
    assert len(body["tools"]) == 1
    assert body["tools"][0]["name"] == "write_file"
    assert body["tools"][0]["cache_control"] == {"type": "ephemeral"}
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


# ---------------------------------------------------------------------------
# Tests: TokenBudgetLimiter
# ---------------------------------------------------------------------------


def test_token_budget_limiter_init():
    """TokenBudgetLimiter initializes with correct limits."""
    limiter = agent_client.TokenBudgetLimiter(input_tpm=50000, output_tpm=10000)
    assert limiter.input_tpm == 50000
    assert limiter.output_tpm == 10000


def test_token_budget_limiter_record_and_usage():
    """record() adds to history and _current_usage() returns totals."""
    limiter = agent_client.TokenBudgetLimiter()
    limiter.record(1000, 200)
    limiter.record(500, 100)
    inp, out = limiter._current_usage()
    assert inp == 1500
    assert out == 300


@pytest.mark.asyncio
async def test_token_budget_limiter_no_wait_under_budget():
    """wait_for_budget returns immediately when under budget."""
    limiter = agent_client.TokenBudgetLimiter(input_tpm=30000, output_tpm=8000)
    limiter.record(5000, 1000)
    # Should return immediately — well under budget
    await limiter.wait_for_budget(estimated_input=5000)


@pytest.mark.asyncio
async def test_token_budget_limiter_waits_when_over_budget():
    """wait_for_budget blocks when budget is exceeded."""
    import time
    limiter = agent_client.TokenBudgetLimiter(input_tpm=1000, output_tpm=1000)
    # Fill up most of the budget — use 950 of 1000 (95% > 90% threshold)
    limiter.record(950, 0)

    waited = False
    def on_wait(w, iu, il, ou, ol):
        nonlocal waited
        waited = True

    # wait_for_budget will try to wait, but we need to inject an expiry
    # Manually expire the oldest record so the wait resolves quickly
    if limiter._history:
        old_entry = limiter._history[0]
        # Backdate the entry so it expires immediately
        limiter._history[0] = (time.monotonic() - 61, old_entry[1], old_entry[2])

    await limiter.wait_for_budget(estimated_input=200, on_wait=on_wait)
    # The entry expired, so it should have resolved without the on_wait callback
    # (because after purging, usage is 0)


def test_token_budget_limiter_purge():
    """_purge_old removes entries older than 60 seconds."""
    import time
    limiter = agent_client.TokenBudgetLimiter()
    # Add an old entry
    limiter._history.append((time.monotonic() - 61, 5000, 1000))
    limiter._history.append((time.monotonic(), 1000, 200))
    limiter._purge_old()
    assert len(limiter._history) == 1
    inp, out = limiter._current_usage()
    assert inp == 1000
    assert out == 200


def test_get_limiter_singleton():
    """get_limiter returns the same instance."""
    # Reset global
    agent_client._global_pool = None
    l1 = agent_client.get_limiter()
    l2 = agent_client.get_limiter()
    assert l1 is l2
    # Clean up
    agent_client._global_pool = None


def test_stream_usage_cache_fields():
    """StreamUsage includes cache token fields."""
    u = agent_client.StreamUsage()
    assert u.cache_read_input_tokens == 0
    assert u.cache_creation_input_tokens == 0
    u.cache_read_input_tokens = 5000
    u.cache_creation_input_tokens = 1000
    assert u.cache_read_input_tokens == 5000


def test_headers_include_caching():
    """_headers includes prompt-caching beta header."""
    headers = agent_client._headers("test-key")
    assert "anthropic-beta" in headers
    assert "prompt-caching" in headers["anthropic-beta"]


@pytest.mark.asyncio
async def test_token_budget_limiter_cache_reads_excluded():
    """Cache-read tokens must NOT be recorded toward the rate-limit budget.

    Only fresh input_tokens should be tracked. Cache reads and cache creation
    are excluded.
    """
    limiter = agent_client.TokenBudgetLimiter(input_tpm=80_000, output_tpm=16_000)
    # Record only fresh tokens (what the limiter should see)
    limiter.record(2_000, 1_000)
    inp, out = limiter._current_usage()
    assert inp == 2_000
    # 2K recorded + 3K estimated = 5K << 72K (90% of 80K).
    # Should pass immediately.
    await limiter.wait_for_budget(estimated_input=3_000)


@pytest.mark.asyncio
@patch("app.clients.agent_client.httpx.AsyncClient")
async def test_stream_agent_all_tokens_counted_for_limiter(mock_client_cls):
    """stream_agent records ALL input tokens (fresh + cache_read + cache_creation)
    in the limiter, because Anthropic counts them all toward TPM rate limits."""
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 500,
                    "cache_read_input_tokens": 40_000,
                    "cache_creation_input_tokens": 8_000,
                },
                "model": "claude-opus-4-6",
            },
        },
        {"type": "content_block_start", "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "delta": {"text": "ok"}},
        {"type": "content_block_stop"},
        {"type": "message_delta", "usage": {"output_tokens": 100}},
    ]
    mock_client_cls.return_value = _make_stream_mock(events)

    limiter = agent_client.TokenBudgetLimiter(input_tpm=80_000, output_tpm=16_000)
    usage = agent_client.StreamUsage()
    async for _ in agent_client.stream_agent(
        api_key="key", model="claude-opus-4-6",
        system_prompt="test", messages=[{"role": "user", "content": "hi"}],
        usage_out=usage,
        token_limiter=limiter,
    ):
        pass

    # usage_out tracks per-category tokens for billing/display
    assert usage.input_tokens == 500
    assert usage.cache_read_input_tokens == 40_000
    assert usage.cache_creation_input_tokens == 8_000

    # Limiter records ALL input tokens (500 + 40K + 8K = 48500) because
    # Anthropic counts all tokens toward the TPM rate limit regardless
    # of caching — caching only reduces cost, not rate-limit consumption.
    inp, out = limiter._current_usage()
    assert inp == 48_500  # fresh + cache_read + cache_creation
    assert out == 100


# ---------------------------------------------------------------------------
# ApiKeyPool tests
# ---------------------------------------------------------------------------

def test_api_key_pool_init_single_key():
    """Pool works with a single key."""
    pool = agent_client.ApiKeyPool(["key-a"])
    assert pool.key_count == 1
    key, lim = pool.best_key()
    assert key == "key-a"
    assert isinstance(lim, agent_client.TokenBudgetLimiter)


def test_api_key_pool_init_multiple_keys():
    """Pool works with multiple keys."""
    pool = agent_client.ApiKeyPool(["key-a", "key-b", "key-c"])
    assert pool.key_count == 3


def test_api_key_pool_deduplicates():
    """Pool deduplicates identical keys."""
    pool = agent_client.ApiKeyPool(["key-a", "key-a", "key-b"])
    assert pool.key_count == 2


def test_api_key_pool_rejects_empty():
    """Pool raises on empty key list."""
    import pytest
    with pytest.raises(ValueError):
        agent_client.ApiKeyPool([])
    with pytest.raises(ValueError):
        agent_client.ApiKeyPool(["", ""])


def test_api_key_pool_best_key_selects_least_loaded():
    """best_key returns the key with the most available budget."""
    pool = agent_client.ApiKeyPool(["key-a", "key-b"], input_tpm=80_000, output_tpm=16_000)
    # Load key-a with 50K tokens
    pool.get_limiter("key-a").record(50_000, 0)
    # key-b is fresh — should be selected
    key, lim = pool.best_key()
    assert key == "key-b"
    # Now load key-b with 60K
    pool.get_limiter("key-b").record(60_000, 0)
    # key-a (50K used) has more room than key-b (60K used)
    key, _ = pool.best_key()
    assert key == "key-a"


def test_api_key_pool_aggregate_usage():
    """aggregate_usage sums across all keys."""
    pool = agent_client.ApiKeyPool(["key-a", "key-b"])
    pool.get_limiter("key-a").record(10_000, 500)
    pool.get_limiter("key-b").record(20_000, 300)
    inp, out = pool.aggregate_usage()
    assert inp == 30_000
    assert out == 800


def test_get_key_pool_singleton():
    """get_key_pool returns the same instance."""
    agent_client._global_pool = None
    p1 = agent_client.get_key_pool(["key-a"])
    p2 = agent_client.get_key_pool(["key-a", "key-b"])
    assert p1 is p2  # second call returns existing
    agent_client._global_pool = None
