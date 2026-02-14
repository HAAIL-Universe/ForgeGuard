"""Tests for webhook signature verification."""

from app.webhooks import _hmac_sha256, verify_github_signature


def test_valid_signature():
    secret = "test-secret"
    payload = b'{"action": "push"}'
    digest = _hmac_sha256(secret.encode(), payload)
    signature = f"sha256={digest}"

    assert verify_github_signature(payload, signature, secret) is True


def test_invalid_signature():
    assert verify_github_signature(b"payload", "sha256=invalid", "secret") is False


def test_missing_prefix():
    assert verify_github_signature(b"payload", "md5=abc", "secret") is False


def test_wrong_secret():
    secret = "correct-secret"
    payload = b'{"action": "push"}'
    digest = _hmac_sha256(secret.encode(), payload)
    signature = f"sha256={digest}"

    assert verify_github_signature(payload, signature, "wrong-secret") is False
