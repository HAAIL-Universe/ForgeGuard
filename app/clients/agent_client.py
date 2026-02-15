"""Agent client -- Claude Agent SDK wrapper for autonomous builder sessions.

Wraps the Anthropic Messages API in streaming mode to simulate an Agent SDK
session.  The caller provides a system prompt (builder directive) and tools
specification; this module handles the HTTP streaming, message assembly, and
yields incremental text chunks so the build service can persist them.

No database access, no business logic, no HTTP framework imports.
"""

from collections.abc import AsyncIterator

import httpx

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


def _headers(api_key: str) -> dict:
    """Build request headers for the Anthropic API."""
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }


async def stream_agent(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 16384,
) -> AsyncIterator[str]:
    """Stream a builder agent session, yielding text chunks.

    Args:
        api_key: Anthropic API key.
        model: Model identifier (e.g. "claude-opus-4-6").
        system_prompt: Builder directive / system instructions.
        messages: Conversation history in Anthropic messages format.
        max_tokens: Maximum tokens for the response.

    Yields:
        Incremental text chunks from the builder agent.

    Raises:
        httpx.HTTPStatusError: On API errors.
        ValueError: On unexpected stream format.
    """
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
        "stream": True,
    }

    async with httpx.AsyncClient(timeout=300.0) as client:
        async with client.stream(
            "POST",
            ANTHROPIC_MESSAGES_URL,
            headers=_headers(api_key),
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]  # strip "data: " prefix
                if data == "[DONE]":
                    break
                # Parse SSE data for content_block_delta events
                try:
                    import json

                    event = json.loads(data)
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {})
                        text = delta.get("text", "")
                        if text:
                            yield text
                except (ValueError, KeyError):
                    # Skip malformed events
                    continue


async def query_agent(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 16384,
) -> str:
    """Non-streaming agent query. Returns the full response text.

    Args:
        api_key: Anthropic API key.
        model: Model identifier.
        system_prompt: Builder directive / system instructions.
        messages: Conversation history.
        max_tokens: Maximum tokens for the response.

    Returns:
        Full response text from the builder agent.

    Raises:
        httpx.HTTPStatusError: On API errors.
        ValueError: On empty or missing text response.
    """
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.post(
            ANTHROPIC_MESSAGES_URL,
            headers=_headers(api_key),
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": messages,
            },
        )
        response.raise_for_status()

    data = response.json()
    content_blocks = data.get("content", [])
    if not content_blocks:
        raise ValueError("Empty response from Anthropic API")

    for block in content_blocks:
        if block.get("type") == "text":
            return block["text"]

    raise ValueError("No text block in Anthropic API response")
