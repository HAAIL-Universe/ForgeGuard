"""Transcription endpoint — accepts audio, returns text via OpenAI Whisper."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.api.deps import get_current_user
from app.clients.transcribe import MAX_AUDIO_BYTES, transcribe_audio
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transcribe", tags=["transcribe"])

# Allowed MIME type prefixes for audio uploads.
_ALLOWED_PREFIXES = ("audio/", "application/octet-stream", "video/webm")


@router.post("")
async def transcribe(
    file: UploadFile,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Accept an audio file and return the Whisper transcript.

    The frontend records voice via MediaRecorder (WebM/Opus) and uploads
    the blob here.  We forward it to OpenAI Whisper and return the text.

    Returns ``{"text": "..."}`` on success.
    """
    # ── Validate API key ────────────────────────────────────────────
    api_key = current_user.get("openai_api_key") or settings.OPENAI_API_KEY
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No OpenAI API key configured for transcription",
        )

    # ── Validate content type ───────────────────────────────────────
    content_type = (file.content_type or "").lower()
    if not any(content_type.startswith(p) for p in _ALLOWED_PREFIXES):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported audio format: {content_type}",
        )

    # ── Read & validate size ────────────────────────────────────────
    audio_data = await file.read()
    if not audio_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Empty audio file",
        )
    if len(audio_data) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio file too large ({len(audio_data)} bytes, max {MAX_AUDIO_BYTES})",
        )

    # ── Transcribe ──────────────────────────────────────────────────
    filename = file.filename or "recording.webm"
    try:
        text = await transcribe_audio(audio_data, api_key, filename=filename)
    except Exception:
        logger.exception("Whisper transcription failed")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Transcription service error",
        )

    return {"text": text}
