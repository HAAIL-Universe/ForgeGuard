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
import time
from collections import deque
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

# Default per-minute token limits (Anthropic Build tier for Opus).
# Cache-read tokens do NOT count toward Anthropic's ITPM, so these
# only need to cover fresh + cache-creation input tokens.
DEFAULT_INPUT_TPM = 80_000
DEFAULT_OUTPUT_TPM = 16_000


# ---------------------------------------------------------------------------
# Token Budget Rate Limiter
# ---------------------------------------------------------------------------


class TokenBudgetLimiter:
    """Sliding-window rate limiter tracking tokens per minute.

    Before each API call, call ``await limiter.wait_for_budget(est_input)``
    to block until enough budget is available.  After each call, call
    ``limiter.record(input_tokens, output_tokens)`` to log actual usage.
    """

    def __init__(
        self,
        input_tpm: int = DEFAULT_INPUT_TPM,
        output_tpm: int = DEFAULT_OUTPUT_TPM,
    ) -> None:
        self.input_tpm = input_tpm
        self.output_tpm = output_tpm
        # Deque of (timestamp, input_tokens, output_tokens)
        self._history: deque[tuple[float, int, int]] = deque()
        self._lock = asyncio.Lock()

    def _purge_old(self) -> None:
        """Remove entries older than 60 seconds."""
        cutoff = time.monotonic() - 60.0
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()

    def _current_usage(self) -> tuple[int, int]:
        """Return (input_tokens, output_tokens) consumed in the last 60s."""
        self._purge_old()
        inp = sum(e[1] for e in self._history)
        out = sum(e[2] for e in self._history)
        return inp, out

    async def wait_for_budget(
        self,
        estimated_input: int = 0,
        on_wait: "Callable[[float, int, int, int, int], Any] | None" = None,
    ) -> None:
        """Block until the minute-window has budget for another API call.

        Only throttles based on *actual recorded usage* from prior calls in the
        sliding window.  If there's no history (first call), always proceeds
        immediately — we never block on estimates alone.

        Args:
            estimated_input: Rough estimate of input tokens for the next call
                             (used only when there IS prior history to compare).
            on_wait: Optional callback(wait_secs, inp_used, inp_limit, out_used, out_limit)
                     fired when throttling is needed.
        """
        async with self._lock:
            while True:
                inp_used, out_used = self._current_usage()

                # No history → first call in the window, always proceed
                if not self._history:
                    return

                # Check both budgets — leave 10% headroom.
                # Only consider the estimate when there are already recorded
                # tokens in the window (prevents deadlock on first call).
                inp_ok = (inp_used + estimated_input) < self.input_tpm * 0.90
                out_ok = out_used < self.output_tpm * 0.90

                if inp_ok and out_ok:
                    return

                # Find when the oldest entry expires to reclaim budget
                oldest_ts = self._history[0][0]
                wait = max(oldest_ts + 60.0 - time.monotonic(), 1.0)

                # Cap wait so we re-check periodically
                wait = min(wait, 15.0)

                if on_wait:
                    try:
                        on_wait(wait, inp_used, self.input_tpm, out_used, self.output_tpm)
                    except Exception:
                        pass

                logger.info(
                    "Token budget limiter: waiting %.1fs "
                    "(input %d/%d, output %d/%d)",
                    wait, inp_used, self.input_tpm, out_used, self.output_tpm,
                )
                await asyncio.sleep(wait)

    def record(self, input_tokens: int, output_tokens: int) -> None:
        """Record actual token usage from a completed API call.

        ``input_tokens`` should only include *fresh* tokens from
        ``usage.input_tokens``.  Cache-creation tokens are a one-time
        cost that won't recur once the prefix is cached, and cache-read
        tokens are discounted — both are excluded so the sliding-window
        limiter doesn't self-block after the initial cache warm-up call.
        """
        self._history.append((time.monotonic(), input_tokens, output_tokens))


# ---------------------------------------------------------------------------
# API Key Pool — round-robin across multiple keys for higher throughput
# ---------------------------------------------------------------------------


class ApiKeyPool:
    """Manages multiple Anthropic API keys, each with its own rate limiter.

    On each call, picks the key whose limiter has the most remaining
    input-token budget (least-loaded).  This lets N keys achieve ~N×
    the throughput of a single key.
    """

    def __init__(
        self,
        api_keys: list[str],
        input_tpm: int = DEFAULT_INPUT_TPM,
        output_tpm: int = DEFAULT_OUTPUT_TPM,
    ) -> None:
        if not api_keys:
            raise ValueError("At least one API key is required")
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for k in api_keys:
            if k and k not in seen:
                seen.add(k)
                unique.append(k)
        if not unique:
            raise ValueError("At least one non-empty API key is required")
        self._keys = unique
        self._limiters = {
            k: TokenBudgetLimiter(input_tpm, output_tpm) for k in unique
        }

    @property
    def key_count(self) -> int:
        return len(self._keys)

    def best_key(self) -> tuple[str, TokenBudgetLimiter]:
        """Return the (api_key, limiter) pair with the most available budget."""
        best_key = self._keys[0]
        best_avail = -1
        for key in self._keys:
            lim = self._limiters[key]
            inp_used, _ = lim._current_usage()
            available = lim.input_tpm - inp_used
            if available > best_avail:
                best_avail = available
                best_key = key
        return best_key, self._limiters[best_key]

    def get_limiter(self, api_key: str) -> TokenBudgetLimiter:
        """Get the limiter for a specific key."""
        return self._limiters[api_key]

    def aggregate_usage(self) -> tuple[int, int]:
        """Return total (input, output) usage across all keys in the window."""
        total_in = total_out = 0
        for lim in self._limiters.values():
            inp, out = lim._current_usage()
            total_in += inp
            total_out += out
        return total_in, total_out


# Global key pool — shared across all builds for the same process.
_global_pool: ApiKeyPool | None = None


def get_key_pool(
    api_keys: list[str],
    input_tpm: int = DEFAULT_INPUT_TPM,
    output_tpm: int = DEFAULT_OUTPUT_TPM,
) -> ApiKeyPool:
    """Get or create the global API key pool."""
    global _global_pool
    if _global_pool is None:
        _global_pool = ApiKeyPool(api_keys, input_tpm, output_tpm)
    return _global_pool


# Backward-compat: single-key helper
def get_limiter(input_tpm: int = DEFAULT_INPUT_TPM, output_tpm: int = DEFAULT_OUTPUT_TPM) -> TokenBudgetLimiter:
    """Get or create a single global limiter (legacy, prefer get_key_pool)."""
    pool = get_key_pool(["default"], input_tpm, output_tpm)
    return pool.get_limiter("default")


@dataclass
class StreamUsage:
    """Accumulates token usage from a streaming session."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
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
        "anthropic-beta": "prompt-caching-2024-07-31",
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
    token_limiter: TokenBudgetLimiter | None = None,
    key_pool: ApiKeyPool | None = None,
) -> AsyncIterator[Union[str, ToolCall]]:
    """Stream a builder agent session, yielding text chunks or tool calls.

    Includes automatic retry with exponential back-off for rate-limit (429)
    and transient server errors (500/502/503/529).

    Args:
        api_key: Anthropic API key (used when key_pool is not provided).
        model: Model identifier (e.g. "claude-opus-4-6").
        system_prompt: Builder directive / system instructions.
        messages: Conversation history in Anthropic messages format.
        max_tokens: Maximum tokens for the response.
        usage_out: Optional StreamUsage to accumulate token counts.
        tools: Optional list of tool definitions in Anthropic format.
        on_retry: Optional callback(status_code, attempt, wait_seconds) called
                  before each retry sleep so the caller can log / broadcast.
        token_limiter: Optional TokenBudgetLimiter for proactive rate control
                       (used when key_pool is not provided).
        key_pool: Optional ApiKeyPool for multi-key rotation.  When provided,
                  overrides api_key and token_limiter — the pool picks the
                  least-loaded key automatically.

    Yields:
        str: Incremental text chunks from the builder agent.
        ToolCall: A tool_use block that the caller should execute.

    Raises:
        httpx.HTTPStatusError: On non-retryable API errors.
        ValueError: On unexpected stream format.
    """
    # If a key pool is provided, select the best key and its limiter
    if key_pool:
        api_key, token_limiter = key_pool.best_key()
        logger.info(
            "Key pool: selected key ...%s (%d keys available)",
            api_key[-6:], key_pool.key_count,
        )
    # Use prompt caching: system prompt as a cacheable block
    system_blocks = [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_blocks,
        "messages": messages,
        "stream": True,
    }
    if tools:
        # Mark the last tool with cache_control so the full tool list
        # is included in the cached prefix (system + tools + first message).
        cached_tools = [t.copy() for t in tools]
        if cached_tools:
            cached_tools[-1]["cache_control"] = {"type": "ephemeral"}
        payload["tools"] = cached_tools

    # Estimate TOTAL input tokens (~4 chars per token).
    # Anthropic counts ALL tokens in the request toward TPM rate limits,
    # including cache-read and cache-creation tokens.  Caching only
    # reduces cost — not rate-limit consumption.  We must include the
    # system prompt and every message block in the estimate.
    est_input = len(system_prompt) // 4  # system prompt tokens
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            est_input += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text", "") or block.get("content", "")
                    est_input += len(str(text)) // 4

    # Proactive token budget check — wait if approaching per-minute limits
    if token_limiter:
        def _on_budget_wait(wait_s: float, inp_used: int, inp_lim: int, out_used: int, out_lim: int):
            if on_retry:
                on_retry(
                    0, 0,  # status 0 = budget pacing (not a real 429)
                    wait_s,
                )
            logger.info(
                "Budget pacing: %.0fs (in=%d/%d out=%d/%d)",
                wait_s, inp_used, inp_lim, out_used, out_lim,
            )
        await token_limiter.wait_for_budget(
            estimated_input=est_input,
            on_wait=_on_budget_wait,
        )

    # Track tokens for this specific call (to record with limiter after)
    call_input_tokens = 0
    call_output_tokens = 0

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
                                u = msg.get("usage", {})
                                inp = u.get("input_tokens", 0)
                                cache_read = u.get("cache_read_input_tokens", 0)
                                cache_create = u.get("cache_creation_input_tokens", 0)
                                usage_out.input_tokens += inp
                                usage_out.cache_read_input_tokens += cache_read
                                usage_out.cache_creation_input_tokens += cache_create
                                usage_out.model = msg.get("model", model)
                                # Record ALL input tokens for rate-limit tracking.
                                # Anthropic counts every token in the request
                                # toward their TPM rate limit, regardless of
                                # whether it was served from cache.  Caching only
                                # reduces cost, not rate-limit consumption.
                                call_input_tokens += inp + cache_read + cache_create

                            # Capture usage from message_delta (output tokens)
                            if etype == "message_delta" and usage_out is not None:
                                u = event.get("usage", {})
                                out = u.get("output_tokens", 0)
                                usage_out.output_tokens += out
                                call_output_tokens += out

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
            if token_limiter:
                token_limiter.record(call_input_tokens, call_output_tokens)
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
