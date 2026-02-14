"""Tests for JWT token creation and decoding."""

import time

import jwt as pyjwt
import pytest

from app.auth import ALGORITHM, create_token, decode_token


@pytest.fixture(autouse=True)
def _set_jwt_secret(monkeypatch):
    """Set a test JWT secret for all auth tests."""
    monkeypatch.setattr("app.auth.settings.JWT_SECRET", "test-secret-key-for-unit-tests")
    monkeypatch.setattr("app.config.settings.JWT_SECRET", "test-secret-key-for-unit-tests")


def test_create_token_returns_string():
    token = create_token("user-123", "octocat")
    assert isinstance(token, str)
    assert len(token) > 0


def test_decode_token_returns_payload():
    token = create_token("user-123", "octocat")
    payload = decode_token(token)
    assert payload["sub"] == "user-123"
    assert payload["github_login"] == "octocat"
    assert "exp" in payload
    assert "iat" in payload


def test_decode_token_rejects_invalid_token():
    with pytest.raises(pyjwt.PyJWTError):
        decode_token("invalid.token.value")


def test_decode_token_rejects_wrong_secret():
    token = pyjwt.encode({"sub": "user-123"}, "wrong-secret", algorithm=ALGORITHM)
    with pytest.raises(pyjwt.PyJWTError):
        decode_token(token)


def test_decode_token_rejects_expired():
    token = pyjwt.encode(
        {"sub": "user-123", "exp": int(time.time()) - 100},
        "test-secret-key-for-unit-tests",
        algorithm=ALGORITHM,
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(token)
