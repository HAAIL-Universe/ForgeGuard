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
