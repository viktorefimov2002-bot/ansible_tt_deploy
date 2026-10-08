"""Real disposable PostgreSQL/Redis and process death at durable boundaries."""

import asyncio
import os
import shutil
import sys
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text

from apps.jobs.ports import TransportUnavailable
from apps.jobs.service import JobService, LostClaim
from apps.jobs.worker import Worker
from tests.disposable import REDIS_CONTAINER, validate_ci_environment
from tests.test_job_redis import redis_transport  # noqa: F401
from tests.test_jobs import create, due, expire
from tests.test_migrations import database  # noqa: F401


@pytest.fixture
def redis_restart_target():
    name = os.environ.get("TTCP_TEST_REDIS_CONTAINER", "")
    if not name and os.environ.get("TTCP_CI") != "1":
        pytest.skip(
            "Run scripts/ci/python_validation.py for mandatory disposable process-restart checks"
        )
    validate_ci_environment(os.environ)
    assert REDIS_CONTAINER.fullmatch(name)
    assert shutil.which("docker"), "Disposable Redis restart checks require Docker"
    return name


async def docker_service(action, name):
    # Exact generated name, never a Compose project inherited from the operator.
    assert action in {"stop", "start", "restart"}
    assert REDIS_CONTAINER.fullmatch(name)
    process = await asyncio.create_subprocess_exec(
        "docker", action, name, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        assert await asyncio.wait_for(process.wait(), 30) == 0, "Redis lifecycle command failed"
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def wait_redis(transport):
    async with asyncio.timeout(15):
        while True:
            try:
                if await transport.redis.ping():
                    return
            except Exception:
                pass
            await asyncio.sleep(0.1)


async def wait_job(jobs, job_id, predicate, process):
    async with asyncio.timeout(15):
        while True:
            saved = await jobs.get(job_id)
            if predicate(saved):
                return saved
            assert process.returncode is None, (
                "Disposable worker exited before completing the probe"
            )
            assert saved.status not in {"failed", "cancelled"}, saved.error_code
            await asyncio.sleep(0.05)


@asynccontextmanager
async def worker_process(engine, transport, *, pause=False):
    validate_ci_environment(os.environ)
    async with engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    # Do not pass deployment/execution settings or secrets to an unrestricted app worker.
    names = {
        "TTCP_POSTGRES_HOST",
        "TTCP_POSTGRES_PORT",
        "TTCP_POSTGRES_DB",
        "TTCP_POSTGRES_USER",
        "TTCP_POSTGRES_PASSWORD",
        "TTCP_REDIS_HOST",
        "TTCP_REDIS_PORT",
        "TTCP_REDIS_PASSWORD",
        "TTCP_TEST_POSTGRES",
        "TTCP_TEST_REDIS",
        "TTCP_TEST_REDIS_CONTAINER",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith("TTCP_") or name in names
    }
    arguments = [sys.executable, "-m", "tests.integration_worker", schema, transport.prefix]
    if pause:
        arguments.append("--pause")
    process = await asyncio.create_subprocess_exec(
        *arguments,
        env=environment,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()


async def test_real_redis_restart_repairs_durable_intent(
    database,  # noqa: F811
    redis_transport,  # noqa: F811
    redis_restart_target,
):
    jobs = JobService(database, redis_transport, redis_transport)
    job = await create(jobs)
    previous = (await redis_transport.redis.info("server"))["run_id"]
    assert await redis_transport.redis.llen(redis_transport.prefix + ":queue") == 1
    await docker_service("restart", redis_restart_target)
    await wait_redis(redis_transport)
    assert (await redis_transport.redis.info("server"))["run_id"] != previous
    # Redis in CI has persistence disabled: queue hints and event streams disappear.
    assert await redis_transport.receive() is None
    assert await redis_transport.read(job.id, 0) == []
    assert (await jobs.get(job.id)).status == "queued"
    await due(jobs, job.id)
    await jobs.dispatch()
    await Worker(jobs).execute(await redis_transport.receive())
    saved = await jobs.get(job.id)
    assert (saved.status, saved.attempts) == ("succeeded", 1)
    assert [event["code"] for event in saved.history] == [
        "queued",
        "running",
        "checkpoint",
        "succeeded",
    ]


async def test_intent_commits_while_real_redis_is_down(
    database,  # noqa: F811
    redis_transport,  # noqa: F811
    redis_restart_target,
):
    jobs = JobService(database, redis_transport, redis_transport)
    try:
        await docker_service("stop", redis_restart_target)
        job = await create(jobs)
        assert (await jobs.get(job.id)).status == "queued"
        with pytest.raises(TransportUnavailable):
            await jobs.dispatch()
    finally:
        await docker_service("start", redis_restart_target)
        await wait_redis(redis_transport)
    await jobs.dispatch()
    await Worker(jobs).execute(await redis_transport.receive())
    saved = await jobs.get(job.id)
    assert saved.status == "succeeded" and saved.attempts == 1


async def test_killed_worker_recovers_and_fences_old_claim(
    database,  # noqa: F811
    redis_transport,  # noqa: F811
    redis_restart_target,
):
    jobs = JobService(database, redis_transport, redis_transport)
    job = await create(jobs)
    async with worker_process(database, redis_transport, pause=True) as process:
        running = await wait_job(jobs, job.id, lambda saved: saved.progress == 50, process)
        assert running.status == "running" and running.attempts == 1
        process.kill()  # No graceful shutdown: durable claim is abandoned.
        assert await asyncio.wait_for(process.wait(), 5) != 0
    saved = await jobs.get(job.id)
    assert saved.status == "running" and saved.claim_token == running.claim_token
    await expire(jobs, job.id)  # Advance the persisted lease boundary without a 90-second sleep.
    with pytest.raises(LostClaim):
        await jobs.complete(job.id, running.claim_token)
    await jobs.recover()
    await due(jobs, job.id)
    async with worker_process(database, redis_transport) as process:
        replacement = await wait_job(
            jobs, job.id, lambda saved: saved.status == "succeeded", process
        )
    assert replacement.attempts == 2
    assert sum(event["code"] == "worker_lost" for event in replacement.history) == 1
    with pytest.raises(LostClaim):
        await jobs.checkpoint(job.id, running.claim_token, 99)


async def test_real_duplicate_delivery_executes_once(database, redis_transport):  # noqa: F811
    jobs = JobService(database, redis_transport, redis_transport)
    job = await create(jobs)
    await redis_transport.enqueue(job.id)
    await redis_transport.enqueue(job.id)
    messages = [await redis_transport.receive() for _ in range(3)]
    assert messages == [job.id] * 3
    await asyncio.gather(*(Worker(jobs).execute(message) for message in messages))
    saved = await jobs.get(job.id)
    assert saved.status == "succeeded" and saved.attempts == 1
    assert sum(event["code"] == "succeeded" for event in saved.history) == 1


async def test_unsafe_worker_loss_never_replays_after_restart(database, redis_transport):  # noqa: F811
    jobs = JobService(database, redis_transport, redis_transport)
    job = await create(jobs, replay_safe=False, cancellable=False)
    await jobs.claim(job.id)
    await expire(jobs, job.id)
    await JobService(database, redis_transport, redis_transport).recover()
    await due(jobs, job.id)
    await jobs.dispatch()
    # Even duplicate broker hints cannot execute an operation with an unknown remote outcome.
    await Worker(jobs).execute(job.id)
    saved = await jobs.get(job.id)
    assert (saved.status, saved.error_code, saved.attempts) == ("failed", "unsafe_outcome", 1)
