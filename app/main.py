"""ForgeGuard -- FastAPI application entry point."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers.audit import router as audit_router
from app.api.routers.auth import router as auth_router
from app.api.routers.builds import router as builds_router
from app.api.routers.health import router as health_router
from app.api.routers.projects import router as projects_router
from app.api.routers.repos import router as repos_router
from app.api.routers.scout import router as scout_router
from app.api.routers.webhooks import router as webhooks_router
from app.api.routers.ws import router as ws_router
from app.clients import github_client, llm_client
from app.config import settings
from app.errors import ForgeError
from app.repos.db import close_pool, get_pool
from app.ws_manager import manager as ws_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    if "pytest" not in sys.modules:
        await get_pool()  # fail-fast if DB unreachable
    await ws_manager.start_heartbeat()
    yield
    await ws_manager.stop_heartbeat()
    await github_client.close_client()
    await llm_client.close_client()
    await close_pool()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="ForgeGuard",
        version="0.1.0",
        description="Repository audit monitoring dashboard",
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
    )

    # Global exception handler -- never leak stack traces to clients.
    @application.exception_handler(Exception)
    async def _unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    # Domain exception handler -- maps ForgeError subclasses to HTTP.
    @application.exception_handler(ForgeError)
    async def _forge_error_handler(
        request: Request, exc: ForgeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc)},
        )

    # Transitional ValueError handler -- centralises the "not found" → 404
    # pattern that was previously duplicated 30+ times across routers.
    # Services will migrate to ForgeError subclasses incrementally.
    @application.exception_handler(ValueError)
    async def _value_error_handler(
        request: Request, exc: ValueError
    ) -> JSONResponse:
        detail = str(exc)
        if "not found" in detail.lower():
            return JSONResponse(status_code=404, content={"detail": detail})
        return JSONResponse(status_code=400, content={"detail": detail})

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(repos_router)
    application.include_router(projects_router)
    application.include_router(builds_router)
    application.include_router(webhooks_router)
    application.include_router(ws_router)
    application.include_router(audit_router)
    application.include_router(scout_router)
    return application


app = create_app()
