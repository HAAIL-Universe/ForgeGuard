"""HTTP access log middleware — emits structured METRIC lines to app.log.

Captures every non-skipped HTTP request/response cycle with method, path,
status code, wall time, user identity (from JWT, no DB call), request ID,
and error detail on 4xx/5xx responses.

Writes to the ``forgeguard.access`` logger which is wired to a separate
RotatingFileHandler in main.py (propagate=False keeps forge.log clean).
"""

from __future__ import annotations

import json
import logging
import time

import jwt as pyjwt
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger("forgeguard.access")

_SKIP_PREFIXES = frozenset({"/health", "/ws", "/static", "/favicon.ico"})


class AccessLogMiddleware:
    """ASGI middleware that logs every HTTP request as a structured METRIC line."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if any(path.startswith(p) for p in _SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        method: str = scope.get("method", "?")
        state: dict = scope.get("state", {})
        request_id: str = state.get("request_id", "-")
        user_id, github_login = _extract_user(scope)
        t0 = time.perf_counter()
        status_code = 0
        error_detail = ""

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code, error_detail
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
            elif message["type"] == "http.response.body" and status_code >= 400:
                body_bytes = message.get("body", b"")
                if body_bytes:
                    try:
                        body = json.loads(body_bytes)
                        error_detail = str(
                            body.get("detail", body.get("error", ""))
                        )[:200]
                    except Exception:
                        pass
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Unhandled exception before response was sent — treat as 500.
            if status_code == 0:
                status_code = 500
            raise
        finally:
            wall_ms = (time.perf_counter() - t0) * 1000
            _emit(
                method, path, status_code, wall_ms,
                user_id, github_login, request_id, error_detail,
            )


def _extract_user(scope: Scope) -> tuple[str, str]:
    """Decode JWT from Authorization header without a DB call.

    Returns (user_id_prefix, github_login) or ("-", "-") on failure.
    """
    headers = dict(scope.get("headers", []))
    auth = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")
    if not auth.startswith("Bearer "):
        return ("-", "-")
    token = auth[7:]
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            audience="forgeguard",
            issuer="forgeguard",
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
        uid = payload.get("sub", "-")
        login = payload.get("github_login", "-")
        return (uid[:8] if uid != "-" else "-", login)
    except Exception:
        return ("-", "-")


def _emit(
    method: str,
    path: str,
    status_code: int,
    wall_ms: float,
    user_id: str,
    github_login: str,
    request_id: str,
    error_detail: str,
) -> None:
    """Emit a structured METRIC line for the HTTP request."""
    parts = [
        "METRIC | type=http_request",
        f"method={method}",
        f"path={path}",
        f"status={status_code}",
        f"wall_ms={wall_ms:.0f}",
        f"user={user_id}",
        f"login={github_login}",
        f"req_id={request_id}",
    ]
    if error_detail:
        # Escape pipe chars in error detail to preserve METRIC format
        parts.append(f"error={error_detail.replace('|', '/')}")
    line = " | ".join(parts)

    if status_code >= 500:
        logger.error(line)
    elif status_code >= 400:
        logger.warning(line)
    else:
        logger.info(line)
