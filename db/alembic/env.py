"""Alembic environment configuration — async mode with asyncpg.

Reads DATABASE_URL from the app config (which loads from .env).
Converts ``postgresql://`` to ``postgresql+asyncpg://`` for SQLAlchemy.
"""

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make project root importable so we can read app.config
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _get_url() -> str:
    """Return the async-compatible database URL."""
    # Prefer the env var directly (allows alembic CLI without importing the app)
    url = os.getenv("DATABASE_URL", "")
    if not url:
        try:
            from app.config import settings
            url = settings.DATABASE_URL
        except Exception:
            raise RuntimeError(
                "DATABASE_URL is not set.  Export it or add it to .env."
            )
    # Convert postgres:// or postgresql:// → postgresql+asyncpg://
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live DB."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:  # type: ignore[no-untyped-def]
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with an async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entry-point for online migrations — wraps the async implementation."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
