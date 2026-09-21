"""Shared URL construction and explicit, caller-owned units of work."""

from contextlib import asynccontextmanager

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from apps.shared.config import DatabaseSettings


def database_url(settings: DatabaseSettings) -> URL:
    return URL.create(
        "postgresql+asyncpg",
        username=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )


@asynccontextmanager
async def transaction(engine: AsyncEngine):
    """Commit on success, roll back on errors/cancellation, always close the session."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        yield session
