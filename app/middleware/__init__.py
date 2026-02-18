"""Middleware package for ForgeGuard.

Exports
-------
* :class:`RequestIDMiddleware` — assigns a unique ID to every HTTP request.
* :func:`setup_exception_handlers` — registers global exception handlers.
"""

import uuid
from typing import Any, Callable

from starlette.types import ASGIApp, Receive, Scope, Send

from .exception_handler import setup_exception_handlers  # noqa: F401


class RequestIDMiddleware:
    """Injects ``X-Request-ID`` into every HTTP request/response cycle.

    If the client already provides one, it is reused (useful for
    distributed tracing).  Otherwise a random UUID-4 is generated.

    WebSocket and lifespan scopes pass through untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # WebSocket / lifespan — pass through without touching.
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID
        headers = dict(scope.get("headers", []))
        request_id = (
            headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())
        )
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: dict) -> None:
            if message["type"] == "http.response.start":
                raw_headers: list = list(message.get("headers", []))
                raw_headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": raw_headers}
            await send(message)

        await self.app(scope, receive, send_with_id)
