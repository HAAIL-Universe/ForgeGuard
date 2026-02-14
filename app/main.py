"""ForgeGuard -- FastAPI application entry point."""

from fastapi import FastAPI

from app.api.routers.health import router as health_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(
        title="ForgeGuard",
        version="0.1.0",
        description="Repository audit monitoring dashboard",
    )
    application.include_router(health_router)
    return application


app = create_app()
