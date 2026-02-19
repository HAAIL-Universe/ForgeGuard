"""Whisper transcription client — sends audio to OpenAI's /v1/audio/transcriptions."""

from __future__ import annotations

import logging

import httpx

from app.clients.llm_client import _get_client

logger = logging.getLogger(__name__)

WHISPER_URL = "https://api.openai.com/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-1"

# Maximum file size we accept (25 MB — OpenAI's limit).
MAX_AUDIO_BYTES = 25 * 1024 * 1024


async def transcribe_audio(
    audio_data: bytes,
    api_key: str,
    *,
    filename: str = "recording.webm",
    language: str = "en",
) -> str:
    """Send *audio_data* to OpenAI Whisper and return the transcript text.

    Parameters
    ----------
    audio_data : bytes
        Raw audio bytes (WebM/Opus, mp3, wav, etc.).
    api_key : str
        OpenAI API key.
    filename : str
        Filename hint sent to the API (determines format detection).
    language : str
        ISO-639-1 language code (default ``"en"``).

    Returns
    -------
    str
        The transcribed text.

    Raises
    ------
    ValueError
        If audio_data is empty or exceeds MAX_AUDIO_BYTES.
    httpx.HTTPStatusError
        If the Whisper API returns a non-2xx response.
    """
    if not audio_data:
        raise ValueError("Empty audio data")
    if len(audio_data) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"Audio file too large ({len(audio_data)} bytes, "
            f"max {MAX_AUDIO_BYTES} bytes)"
        )

    client = _get_client()

    # Whisper requires multipart/form-data with the audio in a `file` field.
    files = {"file": (filename, audio_data, "application/octet-stream")}
    data = {"model": WHISPER_MODEL, "language": language}
    headers = {"Authorization": f"Bearer {api_key}"}

    logger.info(
        "Transcribing %d bytes (%s, lang=%s)",
        len(audio_data),
        filename,
        language,
    )

    response = await client.post(
        WHISPER_URL,
        headers=headers,
        files=files,
        data=data,
        timeout=60.0,
    )
    response.raise_for_status()

    result = response.json()
    text = result.get("text", "").strip()

    logger.info("Transcription complete — %d characters", len(text))
    return text
