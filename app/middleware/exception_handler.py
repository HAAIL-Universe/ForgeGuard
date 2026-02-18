"""Global exception handlers for the FastAPI application.

Catches all unhandled exceptions, logs full stack traces server-side,
and returns structured JSON error responses to clients.  Stack traces
are **never** leaked to the client.
"""

import logging
import uuid

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import ForgeError, format_error_response

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    """Extract the request ID injected by :class:`RequestIDMiddleware`.

    Falls back to a freshly generated UUID-4 if the middleware has not
    run (e.g. during unit tests with a bare ``FastAPI()`` app).
    """
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


# ------------------------------------------------------------------
# Individual exception handlers
# ------------------------------------------------------------------

async def global_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Catch-all for any unhandled exception — returns 500."""
    request_id = _get_request_id(request)
    logger.error(
        "Unhandled exception on %s %s [request_id=%s]",
        request.method,
        request.url.path,
        request_id,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=format_error_response(
            error="Internal Server Error",
            detail="Internal server error",
            request_id=request_id,
        ),
    )


async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Handle Starlette/FastAPI ``HTTPException`` — preserves status code."""
    request_id = _get_request_id(request)
    logger.warning(
        "HTTP %s on %s %s [request_id=%s]: %s",
        exc.status_code,
        request.method,
        request.url.path,
        request_id,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(
            error=str(exc.detail) if exc.detail else "Error",
            detail=str(exc.detail) if exc.detail else None,
            request_id=request_id,
        ),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic / request-validation errors — returns 422."""
    request_id = _get_request_id(request)
    errors = exc.errors()
    logger.warning(
        "Validation error on %s %s [request_id=%s]: %s",
        request.method,
        request.url.path,
        request_id,
        errors,
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=format_error_response(
            error="Validation failed",
            detail=errors,
            request_id=request_id,
        ),
    )


async def forge_error_handler(
    request: Request, exc: ForgeError
) -> JSONResponse:
    """Handle domain :class:`ForgeError` subclasses — maps to HTTP status."""
    request_id = _get_request_id(request)
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(
            error=str(exc),
            detail=str(exc),
            request_id=request_id,
        ),
    )


async def value_error_handler(
    request: Request, exc: ValueError
) -> JSONResponse:
    """Transitional handler — maps ``ValueError`` to 400 / 404.

    Services will migrate to :class:`ForgeError` subclasses incrementally.
    """
    request_id = _get_request_id(request)
    detail = str(exc)
    if "not found" in detail.lower():
        return JSONResponse(
            status_code=404,
            content=format_error_response(
                error="Not Found", detail=detail, request_id=request_id,
            ),
        )
    return JSONResponse(
        status_code=400,
        content=format_error_response(
            error="Bad Request", detail=detail, request_id=request_id,
        ),
    )


# ------------------------------------------------------------------
# Registration helper
# ------------------------------------------------------------------

def setup_exception_handlers(app: FastAPI) -> None:
    """Register all global exception handlers on *app*.

    Call this **after** the app is created but **before** routers are
    included so that every route is covered.
    """
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ForgeError, forge_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ValueError, value_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, global_exception_handler)  # type: ignore[arg-type]
