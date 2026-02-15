"""Tests for LLM client -- Anthropic and OpenAI chat wrappers."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.clients.llm_client import chat, chat_anthropic, chat_openai


def _make_mock_client(response_data):
    """Create a mock httpx.AsyncClient with given response data."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = response_data

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


# ---------------------------------------------------------------------------
# Anthropic tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_success(mock_client_cls):
    """Successful chat returns text content."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "Hello from Haiku!"}],
        "model": "claude-3-5-haiku-20241022",
        "role": "assistant",
    })
    mock_client_cls.return_value = mock_client

    result = await chat(
        api_key="test-key",
        model="claude-3-5-haiku-20241022",
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result == "Hello from Haiku!"
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
    assert body["model"] == "claude-3-5-haiku-20241022"
    assert body["system"] == "You are helpful."


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_empty_content(mock_client_cls):
    """Empty content raises ValueError."""
    mock_client = _make_mock_client({"content": []})
    mock_client_cls.return_value = mock_client

    with pytest.raises(ValueError, match="Empty response"):
        await chat(
            api_key="test-key",
            model="claude-3-5-haiku-20241022",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_no_text_block(mock_client_cls):
    """Response with no text block raises ValueError."""
    mock_client = _make_mock_client({
        "content": [{"type": "tool_use", "id": "xyz", "name": "tool", "input": {}}]
    })
    mock_client_cls.return_value = mock_client

    with pytest.raises(ValueError, match="No text block"):
        await chat(
            api_key="test-key",
            model="claude-3-5-haiku-20241022",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_sends_correct_headers(mock_client_cls):
    """Verify correct headers are sent to Anthropic API."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "ok"}]
    })
    mock_client_cls.return_value = mock_client

    await chat(
        api_key="sk-ant-test123",
        model="claude-3-5-haiku-20241022",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
    )

    call_kwargs = mock_client.post.call_args
    headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
    assert headers["x-api-key"] == "sk-ant-test123"
    assert headers["anthropic-version"] == "2023-06-01"


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_max_tokens_parameter(mock_client_cls):
    """Custom max_tokens is passed through."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "ok"}]
    })
    mock_client_cls.return_value = mock_client

    await chat(
        api_key="test-key",
        model="claude-3-5-haiku-20241022",
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
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_openai_success(mock_client_cls):
    """Successful OpenAI chat returns message content."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "Hello from GPT!"}}],
    })
    mock_client_cls.return_value = mock_client

    result = await chat_openai(
        api_key="sk-test",
        model="gpt-4o",
        system_prompt="You are helpful.",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result == "Hello from GPT!"
    call_kwargs = mock_client.post.call_args
    body = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
    assert body["model"] == "gpt-4o"
    # OpenAI puts system prompt in messages[0]
    assert body["messages"][0] == {"role": "system", "content": "You are helpful."}
    assert body["messages"][1] == {"role": "user", "content": "Hi"}


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_openai_empty_choices(mock_client_cls):
    """Empty choices raises ValueError."""
    mock_client = _make_mock_client({"choices": []})
    mock_client_cls.return_value = mock_client

    with pytest.raises(ValueError, match="Empty response from OpenAI"):
        await chat_openai(
            api_key="sk-test",
            model="gpt-4o",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_openai_no_content(mock_client_cls):
    """Missing content in choice raises ValueError."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": ""}}],
    })
    mock_client_cls.return_value = mock_client

    with pytest.raises(ValueError, match="No content in OpenAI"):
        await chat_openai(
            api_key="sk-test",
            model="gpt-4o",
            system_prompt="System",
            messages=[{"role": "user", "content": "Hi"}],
        )


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_openai_sends_correct_headers(mock_client_cls):
    """Verify correct headers are sent to OpenAI API."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
    })
    mock_client_cls.return_value = mock_client

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
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_dispatches_to_openai(mock_client_cls):
    """chat(provider='openai') routes to OpenAI endpoint."""
    mock_client = _make_mock_client({
        "choices": [{"message": {"role": "assistant", "content": "dispatched"}}],
    })
    mock_client_cls.return_value = mock_client

    result = await chat(
        api_key="sk-test",
        model="gpt-4o",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
        provider="openai",
    )

    assert result == "dispatched"
    call_kwargs = mock_client.post.call_args
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
    assert "openai.com" in url


@pytest.mark.asyncio
@patch("app.clients.llm_client.httpx.AsyncClient")
async def test_chat_defaults_to_anthropic(mock_client_cls):
    """chat() defaults to Anthropic."""
    mock_client = _make_mock_client({
        "content": [{"type": "text", "text": "default"}],
    })
    mock_client_cls.return_value = mock_client

    result = await chat(
        api_key="sk-ant-test",
        model="claude-3-5-haiku-20241022",
        system_prompt="System",
        messages=[{"role": "user", "content": "Hi"}],
    )

    assert result == "default"
    call_kwargs = mock_client.post.call_args
    url = call_kwargs.args[0] if call_kwargs.args else call_kwargs[0][0]
    assert "anthropic.com" in url
