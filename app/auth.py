"""JWT encode/decode utilities for session management."""

from datetime import datetime, timedelta, timezone

import jwt

from app.config import settings

ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24
_JWT_AUD = "forgeguard"
_JWT_ISS = "forgeguard"


def create_token(user_id: str, github_login: str) -> str:
    """Create a JWT token for the given user."""
    payload = {
        "sub": user_id,
        "github_login": github_login,
        "aud": _JWT_AUD,
        "iss": _JWT_ISS,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate a JWT token. Raises jwt.PyJWTError on failure."""
    return jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[ALGORITHM],
        audience=_JWT_AUD,
        issuer=_JWT_ISS,
        options={"require": ["exp", "iat", "sub", "aud", "iss"]},
    )
