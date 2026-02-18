"""Tests for LLM client -- Anthropic and OpenAI chat wrappers."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.clients.llm_client import (
    _retry_on_transient,
    chat,
    chat_anthropic,
    chat_openai,
)


def _make_mock_client(response_data):
    """Create a mock httpx.AsyncClient with given response data."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = response_data

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    return mock_client


# ---------------------------------------------------------------------------
# Anthropic tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_success(mock_get_client):
    """Successful chat returns text content."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "Hello from Haiku!"}],
        "model": "claude-haiku-4-5",
        "role": "assistant",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    })
    mock_get_client.return_value = mock_client

    result = await chat(
        api_key="test-key",
        model="claude-haiku-4-5",
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result["text"] == "Hello from Haiku!"
    assert result["usage"]["input_tokens"] == 10
    assert result["usage"]["output_tokens"] == 20
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    assert body["model"] == "claude-haiku-4-5"
    assert body["system"] == "You are helpful."


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_empty_content(mock_get_client):
    """Empty content raises ValueError."""
    mock_client = _make_mock_client({"content": []})
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError, match="Empty response"):
        await chat(
            api_key="test-key",
            model="claude-haiku-4-5",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_no_text_block(mock_get_client):
    """Response with no text block raises ValueError."""
    mock_client = _make_mock_client({
        "content": [{"type": "tool_use", "id": "xyz", "name": "tool", "input": {}}]
    })
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError, match="No text block"):
        await chat(
            api_key="test-key",
            model="claude-haiku-4-5",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_sends_correct_headers(mock_get_client):
    """Verify correct headers are sent to Anthropic API."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "ok"}]
    })
    mock_get_client.return_value = mock_client

    await chat(
        api_key="sk-ant-test123",
        model="claude-haiku-4-5",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
    )

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
    assert headers["x-api-key"] == "sk-ant-test123"
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_max_tokens_parameter(mock_get_client):
    """Custom max_tokens is passed through."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "ok"}]
    })
    mock_get_client.return_value = mock_client

    await chat(
        api_key="test-key",
        model="claude-haiku-4-5",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
        max_tokens=4096,
    )

    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
    assert body["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# OpenAI tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_openai_success(mock_get_client):
    """Successful OpenAI chat returns message content."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "Hello from GPT!"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 15},
    })
    mock_get_client.return_value = mock_client

    result = await chat_openai(
        api_key="sk-test",
        model="gpt-4o",
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result["text"] == "Hello from GPT!"
    assert result["usage"]["input_tokens"] == 5
    assert result["usage"]["output_tokens"] == 15
    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
    assert body["model"] == "gpt-4o"
    # OpenAI puts system prompt in messages[0]
    assert body["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert body["messages"][1] == {"role": "user", "content": "Hi"}


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_openai_empty_choices(mock_get_client):
    """Empty choices raises ValueError."""
    mock_client = _make_mock_client({"choices": []})
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError, match="Empty response from OpenAI"):
        await chat_openai(
            api_key="sk-test",
            model="gpt-4o",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_openai_no_content(mock_get_client):
    """Missing content in choice raises ValueError."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": ""}}],
    })
    mock_get_client.return_value = mock_client

    with pytest.raises(ValueError, match="No content in OpenAI"):
        await chat_openai(
            api_key="sk-test",
            model="gpt-4o",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_openai_sends_correct_headers(mock_get_client):
    """Verify correct headers are sent to OpenAI API."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    })
    mock_get_client.return_value = mock_client

    await chat_openai(
        api_key="sk-proj-test123",
        model="gpt-4o",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
    )

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
    assert headers["Authorization"] == "Bearer sk-proj-test123"


# ---------------------------------------------------------------------------
# Unified chat() provider dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_dispatches_to_openai(mock_get_client):
    """chat(provider='openai') routes to OpenAI endpoint."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "dispatched"}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0},
    })
    mock_get_client.return_value = mock_client

    result = await chat(
        api_key="sk-test",
        model="gpt-4o",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
        provider="openai",
    )

    assert result["text"] == "dispatched"
    call_kwargs = mock_client.post.call_args
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
    assert "openai.com" in url


@pytest.mark.asyncio
@patch("app.clients.llm_client._get_client")
async def test_chat_defaults_to_anthropic(mock_get_client):
    """chat() defaults to Anthropic."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "default"}],
        "usage": {"input_tokens": 0, "output_tokens": 0},
    })
    mock_get_client.return_value = mock_client

    result = await chat(
        api_key="sk-ant-test",
        model="claude-haiku-4-5",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result["text"] == "default"
    call_kwargs = mock_client.post.call_args
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
    assert "anthropic.com" in url


# ---------------------------------------------------------------------------
# Retry logic tests
# ---------------------------------------------------------------------------


class TestRetryOnTransient:
    """Tests for _retry_on_transient."""

    @pytest.mark.asyncio
    async def test_no_retry_on_success(self):
        factory = AsyncMock(return_value="ok")
        result = await _retry_on_transient(factory)
        assert result == "ok"
        assert factory.await_count == 1

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_timeout(self, mock_sleep):
        factory = AsyncMock(
            side_effect=[httpx.ReadTimeout("timeout"), "recovered"]
        )
        result = await _retry_on_transient(factory, max_retries=3)
        assert result == "recovered"
        assert factory.await_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausts_retries_on_timeout(self, mock_sleep):
        factory = AsyncMock(side_effect=httpx.ReadTimeout("still timing out"))
        with pytest.raises(httpx.ReadTimeout):
            await _retry_on_transient(factory, max_retries=2)
        assert factory.await_count == 3  # initial + 2 retries

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_429(self, mock_sleep):
        resp_429 = MagicMock()
        resp_429.status_code = 429
        factory = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError("rate limit", request=MagicMock(), response=resp_429),
                "ok",
            ]
        )
        result = await _retry_on_transient(factory, max_retries=3)
        assert result == "ok"

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_529(self, mock_sleep):
        resp_529 = MagicMock()
        resp_529.status_code = 529
        factory = AsyncMock(
            side_effect=[
                httpx.HTTPStatusError("overloaded", request=MagicMock(), response=resp_529),
                "ok",
            ]
        )
        result = await _retry_on_transient(factory, max_retries=2)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self):
        resp_400 = MagicMock()
        resp_400.status_code = 400
        factory = AsyncMock(
            side_effect=httpx.HTTPStatusError("bad request", request=MagicMock(), response=resp_400)
        )
        with pytest.raises(httpx.HTTPStatusError):
            await _retry_on_transient(factory, max_retries=3)
        assert factory.await_count == 1

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_anthropic_429_valueerror(self, mock_sleep):
        """Our Anthropic wrapper raises ValueError('Anthropic API 429: ...') for rate limits."""
        factory = AsyncMock(
            side_effect=[
                ValueError("Anthropic API 429: rate limited"),
                "ok",
            ]
        )
        result = await _retry_on_transient(factory, max_retries=2)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_valueerror(self):
        factory = AsyncMock(side_effect=ValueError("Empty response from Anthropic API"))
        with pytest.raises(ValueError, match="Empty response"):
            await _retry_on_transient(factory, max_retries=3)
        assert factory.await_count == 1

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_exponential_backoff(self, mock_sleep):
        factory = AsyncMock(
            side_effect=[
                httpx.ReadTimeout("t1"),
                httpx.ReadTimeout("t2"),
                "ok",
            ]
        )
        result = await _retry_on_transient(factory, max_retries=3, backoff_base=2.0)
        assert result == "ok"
        # first retry: 2^(0+1) = 2s, second retry: 2^(1+1) = 4s
        assert mock_sleep.await_count == 2
        mock_sleep.assert_any_await(2.0)
        mock_sleep.assert_any_await(4.0)

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_read_error(self, mock_sleep):
        """httpx.ReadError (TCP reset) should be retried, not blow through."""
        factory = AsyncMock(
            side_effect=[httpx.ReadError("Connection reset"), "recovered"]
        )
        result = await _retry_on_transient(factory, max_retries=3)
        assert result == "recovered"
        assert factory.await_count == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_retries_on_connect_error(self, mock_sleep):
        """httpx.ConnectError should also be retried."""
        factory = AsyncMock(
            side_effect=[httpx.ConnectError("Connection refused"), "recovered"]
        )
        result = await _retry_on_transient(factory, max_retries=3)
        assert result == "recovered"
        assert factory.await_count == 2

    @pytest.mark.asyncio
    @patch("app.clients.llm_client.asyncio.sleep", new_callable=AsyncMock)
    async def test_exhausts_retries_on_transport_error(self, mock_sleep):
        """Transport errors should raise after max retries."""
        factory = AsyncMock(side_effect=httpx.ReadError("still broken"))
        with pytest.raises(httpx.ReadError):
            await _retry_on_transient(factory, max_retries=2)
        assert factory.await_count == 3  # initial + 2 retries


# ---------------------------------------------------------------------------
# Timeout configuration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_timeout_is_300s():
    """Verify LLM singleton is created with 300s timeout."""
    import app.clients.llm_client as mod
    old = mod._client
    mod._client = None
    try:
        client = mod._get_client()
        assert client.timeout == httpx.Timeout(300.0)
    finally:
        if mod._client is not None:
            await mod._client.aclose()
        mod._client = old


@pytest.mark.asyncio
async def test_openai_timeout_is_300s():
    """Verify LLM singleton is created with 300s timeout (shared with Anthropic)."""
    import app.clients.llm_client as mod
    old = mod._client
    mod._client = None
    try:
        client = mod._get_client()
        assert client.timeout == httpx.Timeout(300.0)
    finally:
        if mod._client is not None:
            await mod._client.aclose()
        mod._client = old
