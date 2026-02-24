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
    # Configure root log level with colored output for terminal readability.
    # ANSI colors: red=errors, yellow=warnings, cyan=debug, green=info, bold white=critical.
    _log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    class _ColorFormatter(logging.Formatter):
        """ANSI-colored log formatter for terminal output."""

        _COLORS = {
            logging.DEBUG:    "\033[36m",     # cyan
            logging.INFO:     "\033[32m",     # green
            logging.WARNING:  "\033[33m",     # yellow
            logging.ERROR:    "\033[31m",     # red
            logging.CRITICAL: "\033[1;31m",   # bold red
        }
        _RESET = "\033[0m"
        _DIM = "\033[2m"

        def format(self, record: logging.LogRecord) -> str:
            color = self._COLORS.get(record.levelno, "")
            ts = self.formatTime(record, "%H:%M:%S")
            name = record.name.split(".")[-1][:20]
            msg = record.getMessage()
            return (
                f"{self._DIM}{ts}{self._RESET} "
                f"{color}{record.levelname:<8s}{self._RESET} "
                f"{self._DIM}[{name:>20s}]{self._RESET} "
                f"{color}{msg}{self._RESET}"
            )

    # Enable ANSI escape codes on Windows 10+ (virtual terminal processing)
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # STD_ERROR_HANDLE = -12, ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32.GetStdHandle(-12)
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass  # Fallback: ANSI codes will show as raw text

    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(_ColorFormatter())
    _handlers: list[logging.Handler] = [_handler]

    # Persistent file log — readable by Claude Code for build analysis.
    # Set LOG_FILE in .env (e.g. LOG_FILE=Z:/ForgeCollection/logs/forge.log).
    if settings.LOG_FILE:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path as _LogPath
        _log_path = _LogPath(settings.LOG_FILE)
        _log_path.parent.mkdir(parents=True, exist_ok=True)

        class _PlainFormatter(logging.Formatter):
            """Plain-text formatter for file logs (no ANSI codes)."""
            def format(self, record: logging.LogRecord) -> str:
                ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
                name = record.name.split(".")[-1][:20]
                msg = record.getMessage()
                return f"{ts} {record.levelname:<8s} [{name:>20s}] {msg}"

        _file_handler = RotatingFileHandler(
            str(_log_path),
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=3,
            encoding="utf-8",
        )
        _file_handler.setFormatter(_PlainFormatter())
        _handlers.append(_file_handler)

    logging.basicConfig(level=_log_level, handlers=_handlers)

    # Quiet noisy loggers — uvicorn access logs flood the terminal
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    if "pytest" not in sys.modules:
        try:
            pool = await get_pool()
            logger.info("Database pool initialised.")
            # Apply any additive schema changes that don't have a formal migration runner.
            # Using IF NOT EXISTS / DO NOTHING so these are safe to re-run on every restart.
            await pool.execute("""
                ALTER TABLE projects
                    ADD COLUMN IF NOT EXISTS cached_plan_json  JSONB,
                    ADD COLUMN IF NOT EXISTS plan_cached_at    TIMESTAMPTZ
            """)
            await pool.execute("""
                ALTER TABLE builds
                    ADD COLUMN IF NOT EXISTS pending_gate        VARCHAR(100),
                    ADD COLUMN IF NOT EXISTS gate_payload         JSONB,
                    ADD COLUMN IF NOT EXISTS gate_registered_at   TIMESTAMPTZ
            """)
            from app.repos.build_repo import interrupt_stale_builds, delete_all_zombie_builds
            from app.repos.scout_repo import interrupt_stale_scout_runs
            _interrupted = await interrupt_stale_builds()
            if _interrupted:
                logger.warning(
                    "Interrupted %d stale build(s) left over from previous server session.",
                    _interrupted,
                )
            _zombies = await delete_all_zombie_builds()
            if _zombies:
                logger.info(
                    "Startup: cleared %d zombie build(s) with no phase progress.",
                    _zombies,
                )
            _stale_scouts = await interrupt_stale_scout_runs()
            if _stale_scouts:
                logger.warning(
                    "Startup: marked %d stale scout run(s) as error.",
                    _stale_scouts,
                )
        except Exception as _db_exc:
            # Neon auto-pauses on the free tier — the first request will
            # reconnect.  Log a warning but don't crash startup.
            logger.warning("DB unavailable at startup (%s) — will retry on first request.", _db_exc)
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
