"""Exercise durable behavior against migrated PostgreSQL, not a mocked repository."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from apps.jobs.ports import TransportUnavailable
from apps.jobs.service import CancellationUnsafe, JobService, LostClaim
from apps.jobs.worker import HANDLERS, Handler, RetryableError, Worker
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Job
from tests.test_migrations import database  # noqa: F401


class MemoryTransport:
    def __init__(self):
        self.queue, self.events = [], {}
        self.available = True

    def check(self):
        if not self.available:
            raise TransportUnavailable

    async def enqueue(self, job_id):
        self.check()
        self.queue.append(job_id)

    async def receive(self):
        self.check()
        return self.queue.pop(0) if self.queue else None

    async def publish(self, job_id, event):
        self.check()
        self.events.setdefault(job_id, []).append(event)

    async def read(self, job_id, after):
        self.check()
        return [e for e in self.events.get(job_id, []) if e["sequence"] > after]


@pytest.fixture
async def jobs(database):  # noqa: F811
    transport = MemoryTransport()
    return JobService(database, transport, transport)


async def create(jobs, **overrides):
    values = dict(
        kind="internal.noop",
        target_type="internal",
        target_id=None,
        created_by=None,
        request_id=str(uuid4()),
        idempotency_key=str(uuid4()),
        replay_safe=True,
        cancellable=True,
    )
    values.update(overrides)
    return await jobs.create(**values)


async def due(jobs, job_id):
    async with jobs.engine.begin() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(available_at=func.now(), dispatch_at=func.now())
        )


async def expire(jobs, job_id):
    async with jobs.engine.begin() as db:
        await db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(lease_until=func.now() - text("interval '1 second'"))
        )


async def test_normal_duplicate_and_concurrent_claim(jobs):
    job = await create(jobs)
    await asyncio.gather(Worker(jobs).execute(job.id), Worker(jobs).execute(job.id))
    await Worker(jobs).execute(job.id)
    saved = await jobs.get(job.id)
    assert (saved.status, saved.progress, saved.attempts) == ("succeeded", 100, 1)
    assert saved.finished_at and saved.started_at
    assert saved.claim_token is None
    assert [e["code"] for e in saved.history] == ["queued", "running", "checkpoint", "succeeded"]


async def test_idempotent_intent_conflict_and_concurrency(jobs):
    key = str(uuid4())
    first, second = await asyncio.gather(
        create(jobs, idempotency_key=key), create(jobs, idempotency_key=key)
    )
    assert first.id == second.id
    with pytest.raises(ValueError, match="different operation"):
        await create(jobs, idempotency_key=key, target_type="different")


async def test_durable_intent_survives_broker_failure_and_loss(jobs):
    jobs.queue.available = False
    job = await create(jobs)
    assert (await jobs.get(job.id)).status == "queued"
    with pytest.raises(TransportUnavailable):
        await jobs.dispatch()
    jobs.queue.available = True
    await jobs.dispatch()
    assert job.id in jobs.queue.queue
    jobs.queue.queue.clear()  # Redis restart/flush after delivery.
    await due(jobs, job.id)
    await jobs.dispatch()
    await Worker(jobs).execute(await jobs.queue.receive())
    assert (await jobs.get(job.id)).status == "succeeded"


async def test_retry_budget_and_safe_errors(jobs):
    async def retry(context):
        raise RetryableError("password=never-persist")

    job = await create(jobs)
    worker = Worker(jobs, {"internal.noop": Handler(retry, True, True)})
    for attempt in range(1, 4):
        await worker.execute(job.id)
        saved = await jobs.get(job.id)
        assert saved.attempts == attempt
        assert saved.status == ("failed" if attempt == 3 else "queued")
        if attempt < 3:
            await worker.execute(job.id)  # A delayed retry cannot execute early.
            assert (await jobs.get(job.id)).attempts == attempt
            await due(jobs, job.id)
    assert "never-persist" not in str(saved.history) + saved.error_message
    assert saved.error_code == "handler_failed"


async def test_permanent_failure_and_unknown_handler(jobs):
    async def fail(context):
        raise RuntimeError("private-key")

    job = await create(jobs)
    await Worker(jobs, {"internal.noop": Handler(fail, True, True)}).execute(job.id)
    assert (await jobs.get(job.id)).status == "failed"
    unknown = await create(jobs, kind="unknown")
    await Worker(jobs).execute(unknown.id)
    assert (await jobs.get(unknown.id)).error_code == "unknown_handler"


async def test_expired_lease_recovery_and_stale_fencing(jobs):
    job = await create(jobs)
    claimed = await jobs.claim(job.id)
    await expire(jobs, job.id)
    with pytest.raises(LostClaim):
        await jobs.complete(job.id, claimed.claim_token)
    await jobs.recover()
    assert (await jobs.get(job.id)).status == "queued"
    await due(jobs, job.id)
    replacement = await jobs.claim(job.id)
    with pytest.raises(LostClaim):
        await jobs.checkpoint(job.id, claimed.claim_token, 99)
    await jobs.complete(job.id, replacement.claim_token)
    assert (await jobs.get(job.id)).attempts == 2


async def test_unsafe_crash_fails_without_replaying(jobs):
    job = await create(jobs, replay_safe=False, cancellable=False)
    await jobs.claim(job.id)
    with pytest.raises(CancellationUnsafe):
        await jobs.cancel(job.id, uuid4(), "request")
    await expire(jobs, job.id)
    await jobs.recover()
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.error_code == "unsafe_outcome"


async def test_lost_worker_does_not_acknowledge_pending_cancellation(jobs):
    job = await create(jobs)
    await jobs.claim(job.id)
    await jobs.cancel(job.id, uuid4(), "cancel-before-crash")
    await expire(jobs, job.id)
    await jobs.recover()
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.error_code == "unsafe_outcome"


async def test_cancel_queued_running_and_audit_idempotency(jobs):
    actor = uuid4()  # Audit actor is intentionally a historical polymorphic reference.
    queued = await create(jobs, cancellable=False)
    await jobs.cancel(queued.id, actor, "cancel-queued")
    await Worker(jobs).execute(queued.id)
    assert (await jobs.get(queued.id)).status == "cancelled"
    assert (await jobs.get(queued.id)).attempts == 0
    running = await create(jobs)

    async def cancel_at_checkpoint(context):
        await jobs.cancel(context.job_id, actor, "cancel-running")
        await context.checkpoint(50)
        pytest.fail("checkpoint should stop execution")

    await Worker(jobs, {"internal.noop": Handler(cancel_at_checkpoint, True, True)}).execute(
        running.id
    )
    await jobs.cancel(running.id, actor, "duplicate")
    assert (await jobs.get(running.id)).status == "cancelled"
    async with transaction(jobs.engine) as db:
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == 2


async def test_history_bound_and_broker_failure_during_execution(jobs):
    job = await create(jobs)
    claim = await jobs.claim(job.id)
    jobs.queue.available = False
    for _ in range(205):
        await jobs.checkpoint(job.id, claim.claim_token, 50)
    await jobs.complete(job.id, claim.claim_token)
    saved = await jobs.get(job.id)
    assert saved.status == "succeeded" and len(saved.history) == 200
    assert saved.history[0]["sequence"] > 1 and saved.log_sequence == 208


async def test_timeout_and_graceful_shutdown(jobs):
    started = asyncio.Event()

    async def slow(context):
        started.set()
        await asyncio.Event().wait()

    handlers = {"internal.noop": Handler(slow, True, True)}
    job = await create(jobs)
    await Worker(jobs, handlers, timeout=0.02).execute(job.id)
    assert (await jobs.get(job.id)).error_code == "timeout"
    await due(jobs, job.id)
    started.clear()
    stop = asyncio.Event()
    task = asyncio.create_task(Worker(jobs, handlers, grace=0.02).run(stop))
    await asyncio.wait_for(started.wait(), 3)
    stop.set()
    await asyncio.wait_for(task, 3)
    saved = await jobs.get(job.id)
    assert saved.status == "queued" and saved.error_code == "shutdown"
    await due(jobs, job.id)
    await Worker(jobs, HANDLERS).execute(job.id)
    assert (await jobs.get(job.id)).status == "succeeded"
