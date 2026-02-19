"""ForgeGuard -- FastAPI application entry point."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers.audit import router as audit_router
from app.api.routers.auth import router as auth_router
from app.api.routers.builds import router as builds_router
from app.api.routers.forge import router as forge_router
from app.api.routers.health import router as health_router
from app.api.routers.mcp import router as mcp_router
from app.api.routers.projects import router as projects_router
from app.api.routers.repos import router as repos_router
from app.api.routers.scout import router as scout_router
from app.api.routers.transcribe import router as transcribe_router
from app.api.routers.webhooks import router as webhooks_router
from app.api.routers.ws import router as ws_router
from app.clients import github_client, llm_client
from app.config import settings
from app.middleware import RequestIDMiddleware
from app.middleware.exception_handler import setup_exception_handlers
from app.repos.db import close_pool, get_pool
from app.services.upgrade_executor import shutdown_all as _shutdown_upgrades
from app.ws_manager import manager as ws_manager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    # Configure root log level from settings
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    )
    if "pytest" not in sys.modules:
        await get_pool()  # fail-fast if DB unreachable
    await ws_manager.start_heartbeat()
    yield
    # Shutdown sequence — order matters:
    # 1. Stop heartbeat (no more WS pings)
    # 2. Cancel all background upgrade/retry/narrate tasks
    #    (must finish before httpx clients are closed)
    # 3. Close HTTP clients
    # 4. Close DB pool
    await ws_manager.stop_heartbeat()
    await _shutdown_upgrades()
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

    # Register all global exception handlers (structured JSON responses
    # with request_id tracing — see app/middleware/exception_handler.py).
    setup_exception_handlers(application)

    application.add_middleware(RequestIDMiddleware)
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
    application.include_router(forge_router)
    application.include_router(mcp_router)
    application.include_router(transcribe_router)
    return application


app = create_app()
