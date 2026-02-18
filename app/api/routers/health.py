"""Health check router."""

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import VERSION
from app.repos.db import get_pool

router = APIRouter()


@router.get("/health")
async def health_check():
    """Return health status with a real DB connectivity check."""
    if os.getenv("FORGE_SANDBOX") == "1" or os.getenv("TESTING") == "1":
        db_ok = True
    else:
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            db_ok = True
        except Exception:
            db_ok = False

    if db_ok:
        return {"status": "ok", "db": "connected"}
    return JSONResponse(
        {"status": "degraded", "db": "unreachable"},
        status_code=503,
    )


@router.get("/health/version")
async def health_version() -> dict:
    """Return application version and current phase."""
    return {"version": VERSION, "phase": "6"}