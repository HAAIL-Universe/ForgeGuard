"""Agent client -- Claude Agent SDK wrapper for autonomous builder sessions.

Wraps the Anthropic Messages API in streaming mode to simulate an Agent SDK
session.  The caller provides a system prompt (builder directive) and tools
specification; this module handles the HTTP streaming, message assembly, and
yields incremental text chunks so the build service can persist them.

No database access, no business logic, no HTTP framework imports.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable, Union

import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

# Retry / rate-limit settings
MAX_RETRIES = 6
BASE_BACKOFF = 2.0            # seconds — exponential: 2, 4, 8, 16, 32, 64
_RETRYABLE_CODES = frozenset({429, 500, 502, 503, 529})


@dataclass
class StreamUsage:
    """Accumulates token usage from a streaming session."""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""


@dataclass
class ToolCall:
    """Represents a tool_use block from the streaming response."""
    id: str
    name: str
    input: dict


def _headers(api_key: str) -> dict:
    """Build request headers for the Anthropic API."""
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }


def _retry_wait(response: httpx.Response | None, attempt: int) -> float:
    """Compute how long to wait before retrying.

    Prefers the ``retry-after`` header from Anthropic (seconds). Falls back
    to exponential backoff: 2, 4, 8, 16, 32, 64 seconds, capped at 90s.
    """
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 120.0)
            except (ValueError, TypeError):
                pass
    return min(BASE_BACKOFF ** (attempt + 1), 90.0)


async def stream_agent(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 16384,
    usage_out: StreamUsage | None = None,
    tools: list[dict] | None = None,
    on_retry: "Callable[[int, int, float], Any] | None" = None,
) -> AsyncIterator[Union[str, ToolCall]]:
    """Stream a builder agent session, yielding text chunks or tool calls.

    Includes automatic retry with exponential back-off for rate-limit (429)
    and transient server errors (500/502/503/529).

    Args:
        api_key: Anthropic API key.
        model: Model identifier (e.g. "claude-opus-4-6").
        system_prompt: Builder directive / system instructions.
        messages: Conversation history in Anthropic messages format.
        max_tokens: Maximum tokens for the response.
        usage_out: Optional StreamUsage to accumulate token counts.
        tools: Optional list of tool definitions in Anthropic format.
        on_retry: Optional callback(status_code, attempt, wait_seconds) called
                  before each retry sleep so the caller can log / broadcast.

    Yields:
        str: Incremental text chunks from the builder agent.
        ToolCall: A tool_use block that the caller should execute.

    Raises:
        httpx.HTTPStatusError: On non-retryable API errors.
        ValueError: On unexpected stream format.
    """
    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": messages,
        "stream": True,
    }
    if tools:
        payload["tools"] = tools

    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        # Track tool_use blocks being accumulated (reset per attempt)
        current_tool_id: str = ""
        current_tool_name: str = ""
        current_tool_json: str = ""
        in_tool_block: bool = False

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                async with client.stream(
                    "POST",
                    ANTHROPIC_MESSAGES_URL,
                    headers=_headers(api_key),
                    json=payload,
                ) as response:
                    if response.status_code in _RETRYABLE_CODES:
                        wait = _retry_wait(response, attempt)
                        if attempt < MAX_RETRIES:
                            logger.warning(
                                "Agent stream %d (attempt %d/%d), retrying in %.1fs",
                                response.status_code, attempt + 1, MAX_RETRIES + 1, wait,
                            )
                            if on_retry:
                                on_retry(response.status_code, attempt + 1, wait)
                            await asyncio.sleep(wait)
                            continue
                        # Last attempt — let raise_for_status raise
                    response.raise_for_status()

                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data = line[6:]  # strip "data: " prefix
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                            etype = event.get("type", "")

                            # Capture usage from message_start
                            if etype == "message_start" and usage_out is not None:
                                msg = event.get("message", {})
                                usage = msg.get("usage", {})
                                usage_out.input_tokens += usage.get("input_tokens", 0)
                                usage_out.model = msg.get("model", model)

                            # Capture usage from message_delta (output tokens)
                            if etype == "message_delta" and usage_out is not None:
                                usage = event.get("usage", {})
                                usage_out.output_tokens += usage.get("output_tokens", 0)

                            # Content block start — detect tool_use blocks
                            if etype == "content_block_start":
                                block = event.get("content_block", {})
                                if block.get("type") == "tool_use":
                                    in_tool_block = True
                                    current_tool_id = block.get("id", "")
                                    current_tool_name = block.get("name", "")
                                    current_tool_json = ""

                            # Content block delta — text or tool input JSON
                            if etype == "content_block_delta":
                                delta = event.get("delta", {})
                                if in_tool_block:
                                    # Accumulate tool input JSON
                                    json_chunk = delta.get("partial_json", "")
                                    if json_chunk:
                                        current_tool_json += json_chunk
                                else:
                                    text = delta.get("text", "")
                                    if text:
                                        yield text

                            # Content block stop — finalize tool call
                            if etype == "content_block_stop" and in_tool_block:
                                in_tool_block = False
                                try:
                                    tool_input = json.loads(current_tool_json) if current_tool_json else {}
                                except json.JSONDecodeError:
                                    tool_input = {"_raw": current_tool_json}
                                yield ToolCall(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                    input=tool_input,
                                )
                                current_tool_id = ""
                                current_tool_name = ""
                                current_tool_json = ""

                        except (ValueError, KeyError):
                            # Skip malformed events
                            continue
            # If we get here, streaming completed successfully
            return

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in _RETRYABLE_CODES and attempt < MAX_RETRIES:
                wait = _retry_wait(exc.response, attempt)
                logger.warning(
                    "Agent stream %d (attempt %d/%d), retrying in %.1fs",
                    exc.response.status_code, attempt + 1, MAX_RETRIES + 1, wait,
                )
                if on_retry:
                    on_retry(exc.response.status_code, attempt + 1, wait)
                await asyncio.sleep(wait)
            else:
                raise
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = _retry_wait(None, attempt)
                logger.warning(
                    "Agent stream timeout (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, MAX_RETRIES + 1, wait,
                )
                if on_retry:
                    on_retry(0, attempt + 1, wait)
                await asyncio.sleep(wait)
            else:
                raise

    if last_exc:
        raise last_exc


async def query_agent(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 16384,
) -> str:
    """Non-streaming agent query. Returns the full response text.

    Includes automatic retry with exponential back-off for rate limits.

    Args:
        api_key: Anthropic API key.
        model: Model identifier.
        system_prompt: Builder directive / system instructions.
        messages: Conversation history.
        max_tokens: Maximum tokens for the response.

    Returns:
        Full response text from the builder agent.

    Raises:
        httpx.HTTPStatusError: On non-retryable API errors.
        ValueError: On empty or missing text response.
    """
    last_exc: Exception | None = None

    for attempt in range(MAX_RETRIES + 1):
        try:
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
                if response.status_code in _RETRYABLE_CODES and attempt < MAX_RETRIES:
                    wait = _retry_wait(response, attempt)
                    logger.warning(
                        "Agent query %d (attempt %d/%d), retrying in %.1fs",
                        response.status_code, attempt + 1, MAX_RETRIES + 1, wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                response.raise_for_status()

            data = response.json()
            content_blocks = data.get("content", [])
            if not content_blocks:
                raise ValueError("Empty response from Anthropic API")

            for block in content_blocks:
                if block.get("type") == "text":
                    return block["text"]

            raise ValueError("No text block in Anthropic API response")

        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in _RETRYABLE_CODES and attempt < MAX_RETRIES:
                wait = _retry_wait(exc.response, attempt)
                logger.warning(
                    "Agent query %d (attempt %d/%d), retrying in %.1fs",
                    exc.response.status_code, attempt + 1, MAX_RETRIES + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                wait = _retry_wait(None, attempt)
                logger.warning(
                    "Agent query timeout (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, MAX_RETRIES + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise

    raise last_exc  # type: ignore[misc]  # pragma: no cover
