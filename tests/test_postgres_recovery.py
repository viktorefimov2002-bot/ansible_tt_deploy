"""The existing API pool reconnects after loss of its real PostgreSQL sessions."""

import asyncio
import secrets
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from apps.api.main import create_app
from apps.shared.config import Settings, load_database_settings
from apps.shared.dependencies import Dependencies
from tests.test_migrations import database  # noqa: F401


async def test_live_api_pool_eventually_recovers_terminated_postgres_connections(
    database,  # noqa: F811
    monkeypatch,
):
    settings = Settings(
        **load_database_settings().model_dump(exclude={"dependency_timeout"}),
        redis_host="127.0.0.1",
        redis_password=secrets.token_hex(32),
        dependency_timeout=1,
    )
    dependencies = Dependencies(settings)
    # This regression exercises the real SQL pool; Redis has its own restart gates.
    monkeypatch.setattr(dependencies.redis, "ping", AsyncMock(return_value=True))
    app = create_app(settings)
    app.state.dependencies = dependencies
    try:
        async with dependencies.engine.connect() as first, dependencies.engine.connect() as second:
            pids = [
                await first.scalar(text("SELECT pg_backend_pid()")),
                await second.scalar(text("SELECT pg_backend_pid()")),
            ]
        async with database.connect() as owner:
            for pid in pids:
                assert await owner.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            consecutive = 0
            async with asyncio.timeout(5):
                while consecutive < 2:
                    assert (await client.get("/healthz")).status_code == 200
                    response = await client.get("/readyz")
                    assert response.status_code in (200, 503)
                    consecutive = consecutive + 1 if response.status_code == 200 else 0
                    await asyncio.sleep(0.01)
        assert app.state.dependencies is dependencies
    finally:
        await dependencies.close()
