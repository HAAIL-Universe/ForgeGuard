"""Tests for the transcription layer — client + router.

Covers:
  - transcribe_audio (Whisper client) — success, empty, oversized, HTTP error
  - POST /transcribe (router) — success, no API key, bad content type, empty
    file, oversized file, Whisper failure
"""

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.clients.transcribe import MAX_AUDIO_BYTES, transcribe_audio


# ===================================================================
# transcribe_audio (client unit tests)
# ===================================================================


class TestTranscribeAudio:
    @pytest.mark.asyncio
    async def test_success(self):
        """Happy path — Whisper returns text."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"text": "Hello world"}
        mock_response.raise_for_status = MagicMock()

        with patch("app.clients.transcribe._get_client") as mock_gc:
            mock_gc.return_value.post = AsyncMock(return_value=mock_response)
            result = await transcribe_audio(b"fake-audio", "sk-test")

        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_empty_audio_raises(self):
        with pytest.raises(ValueError, match="Empty audio data"):
            await transcribe_audio(b"", "sk-test")

    @pytest.mark.asyncio
    async def test_oversized_audio_raises(self):
        big = b"x" * (MAX_AUDIO_BYTES + 1)
        with pytest.raises(ValueError, match="Audio file too large"):
            await transcribe_audio(big, "sk-test")

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        """Non-2xx from Whisper should raise."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "rate limit", request=MagicMock(), response=mock_response
        )

        with patch("app.clients.transcribe._get_client") as mock_gc:
            mock_gc.return_value.post = AsyncMock(return_value=mock_response)
            with pytest.raises(httpx.HTTPStatusError):
                await transcribe_audio(b"audio", "sk-test")

    @pytest.mark.asyncio
    async def test_sends_correct_params(self):
        """Verify the Whisper API is called with correct URL/model/language."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "ok"}
        mock_response.raise_for_status = MagicMock()

        with patch("app.clients.transcribe._get_client") as mock_gc:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_gc.return_value = mock_client

            await transcribe_audio(
                b"data", "sk-key", filename="test.mp3", language="fr"
            )

            call_kwargs = mock_client.post.call_args
            assert "audio/transcriptions" in call_kwargs.args[0]
            assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer sk-key"
            assert call_kwargs.kwargs["data"]["model"] == "whisper-1"
            assert call_kwargs.kwargs["data"]["language"] == "fr"

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"text": "  hello  "}
        mock_response.raise_for_status = MagicMock()

        with patch("app.clients.transcribe._get_client") as mock_gc:
            mock_gc.return_value.post = AsyncMock(return_value=mock_response)
            result = await transcribe_audio(b"audio", "sk-test")

        assert result == "hello"


# ===================================================================
# POST /transcribe (router integration tests)
# ===================================================================


@pytest.fixture()
def _transcribe_app():
    """Minimal FastAPI app with just the transcribe router + fake auth."""
    from app.api.routers.transcribe import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def _fake_user():
    return {"id": "00000000-0000-0000-0000-000000000001", "email": "a@b.com"}


@pytest.fixture()
def client(_transcribe_app, _fake_user):
    """TestClient with auth patched out."""
    from app.api.deps import get_current_user

    _transcribe_app.dependency_overrides[get_current_user] = lambda: _fake_user
    with TestClient(_transcribe_app) as c:
        yield c
    _transcribe_app.dependency_overrides.clear()


class TestTranscribeRouter:
    def test_success(self, client, _fake_user):
        """Happy path — file uploaded, text returned."""
        with patch("app.api.routers.transcribe.settings") as mock_s, \
             patch("app.api.routers.transcribe.transcribe_audio", new_callable=AsyncMock) as mock_t:
            mock_s.OPENAI_API_KEY = "sk-test"
            mock_t.return_value = "Hello from Whisper"

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"fake-audio", "audio/webm")},
            )

        assert resp.status_code == 200
        assert resp.json() == {"text": "Hello from Whisper"}

    def test_no_api_key(self, client, _fake_user):
        """503 when no OpenAI key is configured."""
        with patch("app.api.routers.transcribe.settings") as mock_s:
            mock_s.OPENAI_API_KEY = ""
            _fake_user.pop("openai_api_key", None)

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"audio", "audio/webm")},
            )

        assert resp.status_code == 503
        assert "API key" in resp.json()["detail"]

    def test_unsupported_content_type(self, client):
        """415 for non-audio content types."""
        with patch("app.api.routers.transcribe.settings") as mock_s:
            mock_s.OPENAI_API_KEY = "sk-test"

            resp = client.post(
                "/transcribe",
                files={"file": ("doc.pdf", b"data", "application/pdf")},
            )

        assert resp.status_code == 415

    def test_empty_file(self, client):
        """400 for empty file body."""
        with patch("app.api.routers.transcribe.settings") as mock_s:
            mock_s.OPENAI_API_KEY = "sk-test"

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"", "audio/webm")},
            )

        assert resp.status_code == 400

    def test_oversized_file(self, client):
        """413 when file exceeds MAX_AUDIO_BYTES."""
        with patch("app.api.routers.transcribe.settings") as mock_s:
            mock_s.OPENAI_API_KEY = "sk-test"

            # Use a small file that's just over the limit for quick testing
            with patch("app.api.routers.transcribe.MAX_AUDIO_BYTES", 100):
                resp = client.post(
                    "/transcribe",
                    files={"file": ("recording.webm", b"x" * 101, "audio/webm")},
                )

        assert resp.status_code == 413

    def test_whisper_failure(self, client):
        """502 when the Whisper call raises."""
        with patch("app.api.routers.transcribe.settings") as mock_s, \
             patch("app.api.routers.transcribe.transcribe_audio", new_callable=AsyncMock) as mock_t:
            mock_s.OPENAI_API_KEY = "sk-test"
            mock_t.side_effect = Exception("Whisper exploded")

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"audio", "audio/webm")},
            )

        assert resp.status_code == 502
        assert "Transcription service error" in resp.json()["detail"]

    def test_user_key_takes_precedence(self, client, _fake_user):
        """User's own OpenAI key is preferred over server key."""
        _fake_user["openai_api_key"] = "sk-user-key"

        with patch("app.api.routers.transcribe.settings") as mock_s, \
             patch("app.api.routers.transcribe.transcribe_audio", new_callable=AsyncMock) as mock_t:
            mock_s.OPENAI_API_KEY = "sk-server"
            mock_t.return_value = "text"

            client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"audio", "audio/webm")},
            )

            # Check the key passed to transcribe_audio
            assert mock_t.call_args.args[1] == "sk-user-key"

        del _fake_user["openai_api_key"]

    def test_accepts_octet_stream(self, client):
        """application/octet-stream is allowed (some browsers send this)."""
        with patch("app.api.routers.transcribe.settings") as mock_s, \
             patch("app.api.routers.transcribe.transcribe_audio", new_callable=AsyncMock) as mock_t:
            mock_s.OPENAI_API_KEY = "sk-test"
            mock_t.return_value = "ok"

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"audio", "application/octet-stream")},
            )

        assert resp.status_code == 200

    def test_accepts_video_webm(self, client):
        """video/webm is allowed (Chrome MediaRecorder uses this)."""
        with patch("app.api.routers.transcribe.settings") as mock_s, \
             patch("app.api.routers.transcribe.transcribe_audio", new_callable=AsyncMock) as mock_t:
            mock_s.OPENAI_API_KEY = "sk-test"
            mock_t.return_value = "ok"

            resp = client.post(
                "/transcribe",
                files={"file": ("recording.webm", b"audio", "video/webm")},
            )

        assert resp.status_code == 200
