"""LLM client -- Anthropic Messages API wrapper for questionnaire chat."""

import httpx

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"


def _headers(api_key: str) -> dict:
    """Return standard Anthropic API headers."""
    return {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }


async def chat(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict],
    max_tokens: int = 2048,
) -> str:
    """Send a chat request to the Anthropic Messages API.

    Parameters
    ----------
    api_key : str
        Anthropic API key.
    model : str
        Model identifier (e.g. ``claude-3-5-haiku-20241022``).
    system_prompt : str
        System-level instructions for the model.
    messages : list[dict]
        Conversation history as ``[{"role": "user"|"assistant", "content": str}]``.
    max_tokens : int
        Maximum tokens in the response.

    Returns
    -------
    str
        The assistant's text reply.

    Raises
    ------
    httpx.HTTPStatusError
        If the API returns a non-2xx status.
    ValueError
        If the response body is missing content.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
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

    # Extract text from the first text content block.
    for block in content_blocks:
        if block.get("type") == "text":
            return block["text"]

    raise ValueError("No text block in Anthropic API response")
