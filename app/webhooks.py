"""Webhook signature verification for GitHub webhook payloads."""

import hashlib


def _hmac_sha256(key: bytes, msg: bytes) -> str:
    """Compute HMAC-SHA256 using only hashlib (RFC 2104)."""
    block_size = 64  # SHA-256 block size
    if len(key) > block_size:
        key = hashlib.sha256(key).digest()
    key = key.ljust(block_size, b"\x00")
    o_key_pad = bytes(b ^ 0x5C for b in key)
    i_key_pad = bytes(b ^ 0x36 for b in key)
    return hashlib.sha256(o_key_pad + hashlib.sha256(i_key_pad + msg).digest()).hexdigest()


def verify_github_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify the X-Hub-Signature-256 header from GitHub.

    Returns True if the signature matches the expected HMAC-SHA256 digest.
    """
    if not signature.startswith("sha256="):
        return False

    expected = _hmac_sha256(secret.encode(), payload)
    received = signature[len("sha256="):]
    # Constant-time comparison to prevent timing attacks
    if len(expected) != len(received):
        return False
    result = 0
    for a, b in zip(expected, received):
        result |= ord(a) ^ ord(b)
    return result == 0
