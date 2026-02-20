"""LLM client -- multi-provider chat wrapper (Anthropic + OpenAI)."""

import asyncio
import json as _json
import logging
from typing import Any, Callable, Awaitable

import httpx

logger = logging.getLogger(__name__)

# ── Shared HTTP client (connection pooling) ─────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return (or create) the shared httpx client for LLM API calls."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=300.0)
    return _client


async def close_client() -> None:
    """Close the shared LLM HTTP client.  Called during app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = 6
RETRY_BACKOFF_BASE = 2.0  # seconds — exponential: 2, 4, 8, 16, 32, 64
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


def _compute_wait(exc: httpx.HTTPStatusError | None, attempt: int) -> float:
    """Return seconds to wait before retrying.

    Prefers the ``retry-after`` header for 429s. Falls back to exponential
    backoff capped at 90 seconds.
    """
    if exc is not None and exc.response is not None:
        retry_after = exc.response.headers.get("retry-after")
        if retry_after:
            try:
                return min(float(retry_after), 120.0)
            except (ValueError, TypeError):
                pass
    return min(RETRY_BACKOFF_BASE ** (attempt + 1), 90.0)


async def _retry_on_transient(
    coro_factory,
    *,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = RETRY_BACKOFF_BASE,
):
    """Retry a coroutine factory on transient HTTP / timeout errors.

    ``coro_factory`` is a zero-arg callable that returns a new awaitable each
    time (so we can retry fresh).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Covers ReadError, ConnectError, CloseError, etc.
            last_exc = exc
            if attempt < max_retries:
                wait = min(backoff_base ** (attempt + 1), 90.0)
                logger.warning(
                    "LLM request %s (attempt %d/%d), retrying in %.1fs",
                    type(exc).__name__, attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                wait = _compute_wait(exc, attempt)
                logger.warning(
                    "LLM request %d (attempt %d/%d), retrying in %.1fs",
                    exc.response.status_code, attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
        except ValueError as exc:
            # Re-raise API-level errors that we parsed ourselves (e.g. 429)
            msg = str(exc)
            if any(f"API {code}" in msg for code in _RETRYABLE_STATUS_CODES) and attempt < max_retries:
                last_exc = exc
                wait = min(backoff_base ** (attempt + 1), 90.0)
                logger.warning(
                    "LLM API error (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, max_retries + 1, wait, msg,
                )
                await asyncio.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]  # pragma: no cover

# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


def _anthropic_headers(api_key: str, *, caching: bool = False) -> dict:
    """Return standard Anthropic API headers.

    When *caching* is ``True``, include the ``anthropic-beta`` header
    that enables server-side prompt caching (system + tools + prefix).
    """
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }
    if caching:
        headers["anthropic-beta"] = "prompt-caching-2024-07-31"
    return headers


async def chat_anthropic(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
    tools: list[dict] | None = None,
    thinking_budget: int = 0,
    enable_caching: bool = False,
) -> dict:
    """Send a chat request to the Anthropic Messages API.

    When *tools* is provided the raw API response dict is returned so the
    caller can inspect ``stop_reason`` and ``content`` blocks for tool_use.
    When *tools* is ``None`` (the default) the response is simplified to
    ``{"text": ..., "usage": ...}`` for backward compatibility.

    When *thinking_budget* > 0, extended thinking is enabled.  The API
    returns ``thinking`` content blocks in addition to ``text`` blocks;
    the thinking text is included under the ``"thinking"`` key in the
    simplified response.

    When *enable_caching* is ``True``, the system prompt and tool
    definitions are sent in Anthropic's block format with
    ``cache_control`` markers.  This enables server-side prompt caching
    which reduces input token costs by up to 90 % on cache hits.  The
    API response ``usage`` dict will include ``cache_read_input_tokens``
    and ``cache_creation_input_tokens`` when caching is active.
    """

    async def _call():
        # ── System prompt: plain string or cacheable block ──────
        if enable_caching:
            system_value: str | list[dict] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_value = system_prompt

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_value,
            "messages": messages,
        }

        # ── Tools: mark last tool for caching when enabled ──────
        if tools:
            if enable_caching:
                cached_tools = [t.copy() for t in tools]
                if cached_tools:
                    cached_tools[-1] = {
                        **cached_tools[-1],
                        "cache_control": {"type": "ephemeral"},
                    }
                body["tools"] = cached_tools
            else:
                body["tools"] = tools

        if thinking_budget > 0:
            body["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
            # max_tokens must accommodate thinking + output
            body["max_tokens"] = max(max_tokens, thinking_budget + 4096)

        client = _get_client()
        response = await client.post(
            ANTHROPIC_MESSAGES_URL,
            headers=_anthropic_headers(api_key, caching=enable_caching),
            json=body,
        )
        if response.status_code >= 400:
            try:
                err_body = response.json()
                err_msg = err_body.get("error", {}).get("message", response.text)
            except Exception:
                err_msg = response.text
            raise ValueError(f"Anthropic API {response.status_code}: {err_msg}")

        data = response.json()
        usage = data.get("usage", {})
        content_blocks = data.get("content", [])
        if not content_blocks:
            raise ValueError("Empty response from Anthropic API")

        # If tools were provided, return the full response so the caller
        # can process tool_use blocks and the stop_reason.
        if tools:
            return data

        # Extract text and optional thinking from content blocks
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block["text"])
            elif block.get("type") == "thinking":
                thinking_parts.append(block.get("thinking", ""))

        if not text_parts:
            raise ValueError("No text block in Anthropic API response")

        result: dict = {
            "text": "\n".join(text_parts),
            "usage": {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
            },
            "stop_reason": data.get("stop_reason", "end_turn"),
        }
        if thinking_parts:
            result["thinking"] = "\n".join(thinking_parts)
        return result

    return await _retry_on_transient(_call)


# ---------------------------------------------------------------------------
# Anthropic — streaming (live token progress)
# ---------------------------------------------------------------------------

# Rough chars-per-token estimate for output counting during stream
_CHARS_PER_TOKEN = 4.0


async def chat_anthropic_streaming(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
    enable_caching: bool = False,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict:
    """Stream a chat request to Anthropic, calling *on_progress* as tokens arrive.

    ``on_progress(input_tokens, output_tokens_estimate)`` is called:
      * Once when ``message_start`` provides the exact input token count.
      * Periodically during text streaming with an *estimated* output count
        (derived from character count / ~4 chars per token).
      * Once at the end with the exact output token count from ``message_delta``.

    Returns the same shape as ``chat_anthropic``:
    ``{"text": str, "usage": {"input_tokens": int, "output_tokens": int}}``
    """

    async def _call():
        if enable_caching:
            system_value: str | list[dict] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_value = system_prompt

        body: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system_value,
            "messages": messages,
            "stream": True,
        }

        client = _get_client()
        headers = _anthropic_headers(api_key, caching=enable_caching)

        text_parts: list[str] = []
        input_tokens = 0
        output_tokens = 0
        char_count = 0

        async with client.stream(
            "POST",
            ANTHROPIC_MESSAGES_URL,
            headers=headers,
            json=body,
        ) as response:
            if response.status_code >= 400:
                error_body = await response.aread()
                try:
                    err_data = _json.loads(error_body)
                    err_msg = err_data.get("error", {}).get("message", error_body.decode())
                except Exception:
                    err_msg = error_body.decode()
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    raise httpx.HTTPStatusError(
                        f"Anthropic API {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                raise ValueError(f"Anthropic API {response.status_code}: {err_msg}")

            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data = _json.loads(raw_line[6:])
                event_type = data.get("type", "")

                if event_type == "message_start":
                    usage = data.get("message", {}).get("usage", {})
                    input_tokens = usage.get("input_tokens", 0)
                    if on_progress:
                        await on_progress(input_tokens, 0)

                elif event_type == "content_block_delta":
                    delta = data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        txt = delta.get("text", "")
                        text_parts.append(txt)
                        char_count += len(txt)

                        # Live progress — every text delta
                        if on_progress:
                            est_output = int(char_count / _CHARS_PER_TOKEN)
                            await on_progress(input_tokens, est_output)

                elif event_type == "message_delta":
                    usage = data.get("usage", {})
                    output_tokens = usage.get("output_tokens", 0)
                    # Final exact count
                    if on_progress:
                        await on_progress(input_tokens, output_tokens)

        return {
            "text": "".join(text_parts),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        }

    return await _retry_on_transient(_call)


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _openai_headers(api_key: str) -> dict:
    """Return standard OpenAI API headers."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


async def chat_openai(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> dict:
    """Send a chat request to the OpenAI Chat Completions API."""
    oai_messages = [{"role": "system", "content": system_prompt}]
    oai_messages.extend(messages)

    body: dict = {
        "model": model,
        "messages": oai_messages,
    }
    # Newer OpenAI models (o-series, gpt-4o, etc.) use max_completion_tokens;
    # older models use max_tokens.  Send the right one to avoid 400 errors.
    body["max_completion_tokens"] = max_tokens

    async def _call():
        client = _get_client()
        response = await client.post(
            OPENAI_CHAT_URL,
            headers=_openai_headers(api_key),
            json=body,
        )
        if response.status_code == 400:
            detail = response.json().get("error", {}).get("message", response.text)
            raise ValueError(f"OpenAI API error: {detail}")
        response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise ValueError("Empty response from OpenAI API")

        content = choices[0].get("message", {}).get("content")
        if not content:
            raise ValueError("No content in OpenAI API response")

        usage = data.get("usage", {})
        return {
            "text": content,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            },
        }

    return await _retry_on_transient(_call)


# ---------------------------------------------------------------------------
# Unified entry point (backwards-compatible)
# ---------------------------------------------------------------------------


async def chat(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
    provider: str = "anthropic",
    tools: list[dict] | None = None,
    thinking_budget: int = 0,
    enable_caching: bool = False,
) -> dict:
    """Send a chat request to the configured LLM provider.

    Parameters
    ----------
    api_key : str
        API key for the chosen provider.
    model : str
        Model identifier.
    system_prompt : str
        System-level instructions for the model.
    messages : list[dict]
        Conversation history as ``[{"role": "user"|"assistant", "content": str}]``.
    max_tokens : int
        Maximum tokens in the response.
    provider : str
        ``"openai"`` or ``"anthropic"`` (default).
    tools : list[dict] | None
        Anthropic-format tool definitions. When provided with the Anthropic
        provider the full API response is returned instead of the simplified
        ``{"text": ..., "usage": ...}`` dict so that the caller can inspect
        ``stop_reason`` and tool_use blocks.
    thinking_budget : int
        When > 0, enable Anthropic extended thinking with this token budget.
        Ignored for OpenAI provider.
    enable_caching : bool
        When ``True`` (Anthropic only), enable server-side prompt caching
        for the system prompt and tool definitions. Reduces input token
        costs by up to 90 % on cache hits across multi-round tool loops.
        Ignored for OpenAI provider.

    Returns
    -------
    dict
        ``{"text": str, "usage": {"input_tokens": int, "output_tokens": int}}``
        — or the full API response when *tools* is not None.
    """
    if provider == "openai":
        return await chat_openai(api_key, model, system_prompt, messages, max_tokens)
    return await chat_anthropic(
        api_key, model, system_prompt, messages, max_tokens,
        tools=tools, thinking_budget=thinking_budget,
        enable_caching=enable_caching,
    )


async def chat_streaming(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
    provider: str = "anthropic",
    enable_caching: bool = False,
    on_progress: Callable[[int, int], Awaitable[None]] | None = None,
) -> dict:
    """Streaming variant of ``chat`` with live token progress.

    Falls back to non-streaming ``chat`` for OpenAI (no progress callbacks).
    """
    if provider == "openai":
        return await chat_openai(api_key, model, system_prompt, messages, max_tokens)
    return await chat_anthropic_streaming(
        api_key, model, system_prompt, messages, max_tokens,
        enable_caching=enable_caching,
        on_progress=on_progress,
    )
