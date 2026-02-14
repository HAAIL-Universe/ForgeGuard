"""ForgeGuard -- FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers.auth import router as auth_router
from app.api.routers.health import router as health_router
from app.api.routers.repos import router as repos_router
from app.api.routers.webhooks import router as webhooks_router
from app.api.routers.ws import router as ws_router
from app.config import settings
from app.repos.db import close_pool


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    yield
    await close_pool()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="ForgeGuard",
        version="0.1.0",
        description="Repository audit monitoring dashboard",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.FRONTEND_URL],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health_router)
    application.include_router(auth_router)
    application.include_router(repos_router)
    application.include_router(webhooks_router)
    application.include_router(ws_router)
    return application


app = create_app()
