"""LLM client -- multi-provider chat wrapper (Anthropic + OpenAI)."""

import httpx

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
    async with httpx.AsyncClient(timeout=60.0) as client:
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
    content_blocks = data.get("content", [])
    if not content_blocks:
        raise ValueError("Empty response from Anthropic API")

    for block in content_blocks:
        if block.get("type") == "text":
            return block["text"]

    raise ValueError("No text block in Anthropic API response")


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

    async with httpx.AsyncClient(timeout=60.0) as client:
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

    return content


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
    str
        The assistant's text reply.
    """
    if provider == "openai":
        return await chat_openai(api_key, model, system_prompt, messages, max_tokens)
    return await chat_anthropic(api_key, model, system_prompt, messages, max_tokens)
