"""LLM client -- multi-provider chat wrapper (Anthropic + OpenAI)."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0  # seconds
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 529})


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
        except httpx.TimeoutException as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = backoff_base ** attempt
                logger.warning(
                    "LLM request timeout (attempt %d/%d), retrying in %.1fs",
                    attempt + 1, max_retries + 1, wait,
                )
                await asyncio.sleep(wait)
            else:
                raise
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                wait = backoff_base ** attempt
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
                wait = backoff_base ** attempt
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


def _anthropic_headers(api_key: str) -> dict:
    """Return standard Anthropic API headers."""
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }


async def chat_anthropic(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> str:
    """Send a chat request to the Anthropic Messages API."""

    async def _call():
        async with httpx.AsyncClient(timeout=300.0) as client:
            response = await client.post(
                ANTHROPIC_MESSAGES_URL,
                headers=_anthropic_headers(api_key),
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "system": system_prompt,
                    "messages": messages,
                },
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

        for block in content_blocks:
            if block.get("type") == "text":
                return {
                    "text": block["text"],
                    "usage": {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    },
                }

        raise ValueError("No text block in Anthropic API response")

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
) -> str:
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
        async with httpx.AsyncClient(timeout=300.0) as client:
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
) -> str:
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

    Returns
    -------
    dict
        ``{"text": str, "usage": {"input_tokens": int, "output_tokens": int}}``
    """
    if provider == "openai":
        return await chat_openai(api_key, model, system_prompt, messages, max_tokens)
    return await chat_anthropic(api_key, model, system_prompt, messages, max_tokens)
