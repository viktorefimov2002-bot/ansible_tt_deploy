import os
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError

from apps.jobs.ports import TransportUnavailable
from apps.jobs.redis import RedisTransport
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.shared.config import load_settings
from tests.test_jobs import create, due
from tests.test_migrations import database  # noqa: F401


async def test_redis_errors_are_transport_errors_without_secret_text():
    client = AsyncMock()
    client.rpop.side_effect = ConnectionError("password=secret")
    client.xrange.side_effect = ConnectionError("password=secret")
    transport = RedisTransport(client)
    for operation in (transport.receive(), transport.read(uuid4(), 0)):
        with pytest.raises(TransportUnavailable) as failure:
            await operation
        assert "secret" not in str(failure.value)
    client.rpop.side_effect = None
    client.rpop.return_value = b"invalid-id"
    assert await transport.receive() is None


async def test_redis_real_connection_refusal():
    # Bind a socket without listening so the chosen local port cannot be reused.
    import socket

    with socket.socket() as socket_guard:
        socket_guard.bind(("127.0.0.1", 0))
        client = Redis(
            host="127.0.0.1",
            port=socket_guard.getsockname()[1],
            socket_connect_timeout=0.1,
            socket_timeout=0.1,
        )
        transport = RedisTransport(client)
        try:
            for operation in (
                transport.enqueue(uuid4()),
                transport.receive(),
                transport.publish(uuid4(), {"sequence": 1}),
                transport.read(uuid4(), 0),
            ):
                with pytest.raises(TransportUnavailable):
                    await operation
        finally:
            await client.aclose()


@pytest.fixture
async def redis_transport():
    if os.environ.get("TTCP_TEST_REDIS") != "1":
        pytest.skip("Set TTCP_TEST_REDIS=1 and TTCP_REDIS_* for Redis integration")
    settings = load_settings()
    client = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password.get_secret_value(),
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    prefix = "ttcp:test:" + uuid4().hex
    try:
        await client.ping()
        yield RedisTransport(client, prefix)
    finally:
        # Never flush a shared Redis database; remove only this test's namespace.
        async for key in client.scan_iter(match=prefix + ":*", count=100):
            await client.delete(key)
        await client.aclose()


async def test_real_redis_queue_logs_retention_and_loss(database, redis_transport):  # noqa: F811
    jobs = JobService(database, redis_transport, redis_transport)
    job = await create(jobs)
    assert await redis_transport.receive() == job.id
    await due(jobs, job.id)
    await jobs.dispatch()
    # Exact equivalent of loss of our ephemeral keys, without flushing other users.
    await redis_transport.redis.delete(redis_transport.prefix + ":queue")
    await due(jobs, job.id)
    await jobs.dispatch()
    await Worker(jobs).execute(await redis_transport.receive())
    assert (await jobs.get(job.id)).status == "succeeded"
    assert any(e["code"] == "succeeded" for e in await redis_transport.read(job.id, 0))
    key = f"{redis_transport.prefix}:log:{job.id}"
    for sequence in range(10, 215):
        await redis_transport.publish(job.id, {"sequence": sequence})
    assert await redis_transport.redis.xlen(key) == 200
    assert 0 < await redis_transport.redis.ttl(key) <= 3600
    await redis_transport.redis.delete(key)
    assert await redis_transport.read(job.id, 0) == []
    assert (await jobs.get(job.id)).history[-1]["code"] == "succeeded"
