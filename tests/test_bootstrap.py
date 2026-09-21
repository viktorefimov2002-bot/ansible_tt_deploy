import asyncio
import json
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from apps.shared.config import ConfigurationError, load_settings
from apps.shared.dependencies import Dependencies, DependencyUnavailable, connected_dependencies
from apps.shared.logging import JsonFormatter
from apps.worker.__main__ import run


def test_required_configuration_is_safe(settings, monkeypatch):
    monkeypatch.setenv("TTCP_POSTGRES_PORT", "secret-value")
    with pytest.raises(ConfigurationError) as error:
        load_settings()
    assert "TTCP_POSTGRES_PASSWORD" in str(error.value)
    assert "TTCP_POSTGRES_PORT" in str(error.value)
    assert "secret-value" not in str(error.value)
    assert "test-only" not in repr(settings)


@pytest.mark.parametrize(
    "field,value",
    [
        ("postgres_password", ""),
        ("redis_password", "  "),
        ("postgres_host", " "),
        ("redis_port", 0),
        ("postgres_port", 65536),
        ("dependency_timeout", float("nan")),
        ("worker_check_interval", 0),
        ("log_level", "verbose"),
    ],
)
def test_invalid_settings(settings, field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        type(settings)(**(settings.model_dump() | {field: value}))


async def test_api_readiness_recovers_and_liveness_stays_up(settings, monkeypatch):
    dependencies = AsyncMock()
    dependencies.check.return_value = {"postgres": True, "redis": True}
    closed = []

    @asynccontextmanager
    async def connection(_):
        try:
            yield dependencies
        finally:
            closed.append(True)

    monkeypatch.setattr("apps.api.main.connected_dependencies", connection)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/api/readyz")).json() == {"status": "ready"}
        for failed in ("postgres", "redis"):
            dependencies.check.return_value = {"postgres": True, "redis": True} | {failed: False}
            assert (await client.get("/readyz")).status_code == 503
            assert (await client.get("/api/readyz")).json() == {"status": "not_ready"}
            assert (await client.get("/healthz")).status_code == 200
            assert (await client.get("/api/healthz")).status_code == 200
        dependencies.check.return_value = {"postgres": True, "redis": True}
        assert (await client.get("/readyz")).status_code == 200
        assert (await client.get("/api/servers")).status_code == 404
        assert (await client.post("/api/healthz")).status_code == 405
    assert closed == [True]


@pytest.mark.parametrize(
    "checks",
    [
        {"postgres": True, "redis": True},
        {"postgres": False, "redis": True},
        {"postgres": True, "redis": False},
    ],
)
async def test_dependency_lifecycle_closes_on_startup_failure(settings, monkeypatch, checks):
    dependencies = AsyncMock()
    dependencies.check.return_value = checks
    monkeypatch.setattr("apps.shared.dependencies.Dependencies", lambda _: dependencies)
    if all(checks.values()):
        async with connected_dependencies(settings):
            pass
    else:
        with pytest.raises(DependencyUnavailable):
            async with connected_dependencies(settings):
                pytest.fail("must fail before yielding")
    dependencies.close.assert_awaited_once()


async def test_probes_timeout_redact_and_recover(settings, monkeypatch, caplog):
    dependencies = Dependencies(settings)
    postgres = AsyncMock(side_effect=RuntimeError("password=do-not-log"))
    redis = AsyncMock(return_value=True)
    monkeypatch.setattr(dependencies, "_postgres", postgres)
    monkeypatch.setattr(dependencies.redis, "ping", redis)
    try:
        assert await dependencies.check() == {"postgres": False, "redis": True}
        assert "do-not-log" not in caplog.text
        postgres.side_effect = None
        assert all((await dependencies.check()).values())

        async def slow():
            await asyncio.Event().wait()

        monkeypatch.setattr(dependencies.redis, "ping", slow)
        checks = await asyncio.wait_for(dependencies.check(), timeout=1)
        assert checks == {"postgres": True, "redis": False}
    finally:
        await dependencies.close()


async def test_worker_stop_closes_resources(settings, monkeypatch, caplog):
    async def consume(self, stop):
        await stop.wait()

    monkeypatch.setattr("apps.worker.__main__.Worker.run", consume)
    dependencies = AsyncMock()
    entered = asyncio.Event()
    closed = []

    @asynccontextmanager
    async def connection(_):
        try:
            entered.set()
            yield dependencies
        finally:
            closed.append(True)

    monkeypatch.setattr("apps.worker.__main__.connected_dependencies", connection)
    caplog.set_level(logging.INFO)
    stop = asyncio.Event()
    task = asyncio.create_task(run(settings, stop))
    await entered.wait()
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert closed == [True]
    assert "worker_stopped" in caplog.text


def test_structured_logging_omits_exception_and_unapproved_fields():
    record = logging.LogRecord("test", logging.ERROR, "", 0, "dependency_unavailable", (), None)
    record.exc_info = (ValueError, ValueError("password=do-not-log"), None)
    record.password = "do-not-log"
    record.dependency = "postgres"
    rendered = JsonFormatter("worker").format(record)
    assert "do-not-log" not in rendered
    data = json.loads(rendered)
    assert data["service"] == "worker"
    assert data["dependency"] == "postgres"
    assert data["timestamp"]


async def test_worker_monitors_loss_and_recovery(settings, monkeypatch, caplog):
    async def consume(self, stop):
        await stop.wait()

    monkeypatch.setattr("apps.worker.__main__.Worker.run", consume)
    stop = asyncio.Event()
    calls = 0

    async def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            stop.set()
        return {"postgres": True, "redis": calls == 2}

    dependencies = AsyncMock()
    dependencies.check.side_effect = check

    @asynccontextmanager
    async def connection(_):
        yield dependencies

    monkeypatch.setattr("apps.worker.__main__.connected_dependencies", connection)
    caplog.set_level(logging.INFO)
    await asyncio.wait_for(run(settings, stop), timeout=5)
    states = [r.state for r in caplog.records if r.message == "worker_readiness_changed"]
    assert states == ["not_ready", "ready"]


async def test_dependency_cleanup_on_cancellation(settings, monkeypatch):
    dependencies = AsyncMock()
    dependencies.check.return_value = {"postgres": True, "redis": True}
    monkeypatch.setattr("apps.shared.dependencies.Dependencies", lambda _: dependencies)
    with pytest.raises(asyncio.CancelledError):
        async with connected_dependencies(settings):
            raise asyncio.CancelledError
    dependencies.close.assert_awaited_once()


async def test_postgres_disposed_even_if_redis_close_fails(settings, monkeypatch):
    dependencies = Dependencies(settings)
    dispose = AsyncMock(wraps=dependencies.engine.dispose)
    monkeypatch.setattr(dependencies, "engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(dependencies.redis, "aclose", AsyncMock(side_effect=RuntimeError))
    with pytest.raises(RuntimeError):
        await dependencies.close()
    dispose.assert_awaited_once()
