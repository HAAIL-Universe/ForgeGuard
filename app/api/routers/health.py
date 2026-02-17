"""Health check router."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import VERSION
from app.repos.db import get_pool

router = APIRouter()


@router.get("/health")
async def health_check():
    """Return health status with a real DB connectivity check."""
    try:
        pool = await get_pool()
        await pool.fetchval("SELECT 1")
        return {"status": "ok", "db": "connected"}
    except Exception:
        return JSONResponse(
            {"status": "degraded", "db": "unreachable"},
            status_code=503,
        )


@router.get("/health/version")
async def health_version() -> dict:
    """Return application version and current phase."""
    return {"version": VERSION, "phase": "6"}
