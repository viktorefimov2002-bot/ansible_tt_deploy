"""Bounded, read-only connectivity probes and lifespan-owned connection pools."""

import asyncio
import logging
from contextlib import asynccontextmanager

from redis.asyncio import Redis
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import create_async_engine

from apps.shared.config import Settings

logger = logging.getLogger(__name__)


class DependencyUnavailable(RuntimeError):
    pass


class Dependencies:
    def __init__(self, settings: Settings):
        self.timeout = settings.dependency_timeout
        self.engine = create_async_engine(
            URL.create(
                "postgresql+asyncpg",
                username=settings.postgres_user,
                password=settings.postgres_password.get_secret_value(),
                host=settings.postgres_host,
                port=settings.postgres_port,
                database=settings.postgres_db,
            ),
            pool_size=2,
            max_overflow=0,
            pool_timeout=self.timeout,
            pool_pre_ping=True,
            hide_parameters=True,
            connect_args={"timeout": self.timeout, "command_timeout": self.timeout},
        )
        self.redis = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password.get_secret_value(),
            socket_connect_timeout=self.timeout,
            socket_timeout=self.timeout,
            max_connections=4,
            retry_on_timeout=False,
        )

    async def _postgres(self):
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def _probe(self, name, operation):
        try:
            async with asyncio.timeout(self.timeout):
                await operation()
            return True
        except Exception as exc:
            logger.warning(
                "dependency_unavailable",
                extra={"dependency": name, "error_type": type(exc).__name__},
            )
            return False

    async def check(self) -> dict[str, bool]:
        postgres, redis = await asyncio.gather(
            self._probe("postgres", self._postgres), self._probe("redis", self.redis.ping)
        )
        return {"postgres": postgres, "redis": redis}

    async def close(self):
        try:
            await self.redis.aclose()
        finally:
            await self.engine.dispose()


@asynccontextmanager
async def connected_dependencies(settings: Settings):
    dependencies = Dependencies(settings)
    try:
        checks = await dependencies.check()
        failed = [name for name, healthy in checks.items() if not healthy]
        if failed:
            raise DependencyUnavailable("Unavailable dependencies: " + ", ".join(failed))
        yield dependencies
    finally:
        await dependencies.close()
