"""Health check router."""

from fastapi import APIRouter

from app.config import VERSION

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    """Return basic health status."""
    return {"status": "ok"}


@router.get("/health/version")
async def health_version() -> dict:
    """Return application version and current phase."""
    return {"version": VERSION, "phase": "6"}
