"""Alembic uses the same environment-only PostgreSQL settings as the apps."""

import asyncio

from alembic import context
from alembic.util import CommandError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from apps.persistence.database import database_url
from apps.persistence.models import Base
from apps.shared.config import ConfigurationError, load_database_settings


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    settings = load_database_settings()
    engine = create_async_engine(
        database_url(settings),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args={"timeout": settings.dependency_timeout},
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


try:
    if context.is_offline_mode():
        # Offline SQL never needs a password, a running database, or Redis.
        context.configure(
            dialect_name="postgresql",
            target_metadata=Base.metadata,
            literal_binds=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    elif connection := context.config.attributes.get("connection"):
        run_migrations(connection)
    else:
        asyncio.run(online())
except ConfigurationError as exc:
    raise CommandError(str(exc)) from None
except Exception:
    # Driver errors may contain URLs or SQL values. Do not expose those on the CLI.
    raise CommandError("Migration failed; check database connectivity and schema state.") from None
