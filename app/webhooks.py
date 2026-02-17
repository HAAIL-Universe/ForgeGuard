"""Webhook signature verification for GitHub webhook payloads."""

import hashlib
import hmac as _hmac


def _hmac_sha256(key: bytes, msg: bytes) -> str:
    """Compute HMAC-SHA256 digest as a hex string."""
    return _hmac.new(key, msg, hashlib.sha256).hexdigest()


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header from GitHub.

    Returns True if the signature matches the expected HMAC-SHA256 digest.
    Uses ``hmac.compare_digest`` for constant-time comparison.
    """
    if not signature.startswith("sha256="):
        return False

    expected = f"sha256={_hmac_sha256(secret.encode(), payload)}"
    return _hmac.compare_digest(expected, signature)
