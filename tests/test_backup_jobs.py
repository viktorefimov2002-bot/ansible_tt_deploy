"""Durable lifecycle, fencing, RBAC and notification semantics with real PostgreSQL."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from apps.api.main import create_app
from apps.backups.archive import EncryptArchive
from apps.backups.config import BackupFailure
from apps.backups.service import BackupBusy, BackupService
from apps.backups.storage import S3Storage
from apps.jobs.service import JobService, LostClaim
from apps.jobs.worker import Context, Worker
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Job
from apps.visibility.service import VisibilityService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_backups import ObjectStore, backup_config  # noqa: F401
from tests.test_jobs import MemoryTransport, due, expire
from tests.test_migrations import database  # noqa: F401


class Dump:
    def __init__(self):
        self.calls = 0

    async def dump(self, file, config, identity):
        self.calls += 1
        writer = EncryptArchive(file, config, identity)
        writer.update(b"PGDMPtest-only-database")
        writer.finish()


def services(engine):
    transport = MemoryTransport()
    jobs = JobService(engine, transport, transport)
    return jobs, BackupService(jobs, enabled=True)


async def test_api_rbac_validation_idempotency_history_and_no_restore(auth):  # noqa: F811
    jobs, backups = services(auth.engine)
    app = create_app()
    app.state.auth, app.state.jobs, app.state.backups = auth, jobs, backups
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, credentials, _ = await account(auth)
        admin = await login(client, credentials)
        _, credentials, _ = await account(auth, "viewer")
        viewer = await login(client, credentials)
        payload = {"idempotency_key": str(uuid4())}
        assert (await client.get("/api/backups")).status_code == 401
        assert (await client.post("/api/backups/run", json=payload)).status_code == 401
        assert (
            await client.post("/api/backups/run", headers=viewer, json=payload)
        ).status_code == 403
        for bad in (
            payload | {"bucket": "attacker"},
            payload | {"command": "sh"},
            {},
            {"idempotency_key": "secret"},
        ):
            assert (
                await client.post("/api/backups/run", headers=admin, json=bad)
            ).status_code == 422
        first, repeated = await asyncio.gather(
            *[client.post("/api/backups/run", headers=admin, json=payload) for _ in range(2)]
        )
        assert first.status_code == repeated.status_code == 202
        assert first.json()["id"] == repeated.json()["id"]
        assert first.json()["status"] == "queued"
        assert (
            await client.post(
                "/api/backups/run", headers=admin, json={"idempotency_key": str(uuid4())}
            )
        ).status_code == 409
        history = await client.get("/api/backups", headers=viewer)
        assert history.status_code == 200 and history.headers["cache-control"] == "no-store"
        assert history.json()["total"] == 1 and history.json()["age_seconds"] is None
        assert (await client.get("/api/backups?limit=0", headers=viewer)).status_code == 422
        for path in ("/api/backups/restore", "/api/backups/" + first.json()["id"] + "/restore"):
            assert (await client.post(path, headers=admin, json={})).status_code == 404
        job = await jobs.get(UUID(first.json()["id"]))
        assert (
            job.parameters == {}
            and job.max_attempts == 3
            and job.replay_safe
            and not job.cancellable
        )
        backups.enabled = False
        assert (
            await client.post("/api/backups/run", headers=admin, json=payload)
        ).status_code == 503


async def test_lost_upload_response_retries_remote_receipt_without_dump(database, backup_config):  # noqa: F811
    jobs, backups = services(database)
    peer, tools = ObjectStore(), Dump()
    peer.lose_reply = True
    storage = S3Storage(backup_config, transport=httpx.MockTransport(peer.handle))
    job = await backups.request(None, "request", uuid4())
    worker = Worker(jobs, {"backup.run": backups.handler(backup_config, storage, tools)})
    try:
        # Redis is lost; business intent and readback still survive in PostgreSQL.
        jobs.queue.available = False
        await worker.execute(job.id)
        first = await jobs.get(job.id)
        assert first.status == "queued" and first.error_code == "backup_unknown"
        await due(jobs, job.id)
        await worker.execute(job.id)
        await worker.execute(job.id)  # Duplicate hint is ignored.
        done = await jobs.get(job.id)
        assert done.status == "succeeded" and tools.calls == 1
        assert done.result["phase"] == "verified" and len(done.result["sha256"]) == 64
        assert len(peer.objects) == 1
        status = await backups.history()
        assert status["last_success"]["id"] == job.id and status["age_seconds"] >= 0
        async with transaction(database) as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "backup.succeeded")
                )
                == 1
            )
        notifications = await VisibilityService(database).notifications(uuid4())
        assert notifications["items"][0]["type"] == "BACKUP_SUCCEEDED"
        assert "test-secret" not in str(done.history) + str(done.result) + str(notifications)
    finally:
        await storage.close()


async def test_failed_unknown_retry_budget_worker_loss_and_late_fence(database, backup_config):  # noqa: F811
    jobs, backups = services(database)
    job = await backups.request(None, "failed", uuid4())

    class Failure:
        async def dump(self, *args):
            raise BackupFailure()

    peer = ObjectStore()
    storage = S3Storage(backup_config, transport=httpx.MockTransport(peer.handle))
    try:
        await Worker(
            jobs, {"backup.run": backups.handler(backup_config, storage, Failure())}
        ).execute(job.id)
        assert (await backups.history())["latest"]["status"] == "failed"
        notices = await VisibilityService(database).notifications(uuid4())
        assert notices["items"][0]["type"] == "BACKUP_FAILED"
        assert notices["items"][0]["target_id"] == job.id

        lost = await backups.request(None, "lost", uuid4())
        for attempt in range(3):
            claim = await jobs.claim(lost.id)
            context = Context(jobs, lost.id, claim.claim_token, claim.attempts)
            await backups.phase(context, "uploading")
            await expire(jobs, lost.id)
            await jobs.recover()
            with pytest.raises(LostClaim):
                await backups.phase(
                    context,
                    "verified",
                    {"sha256": "a" * 64, "encrypted_bytes": 100, "key_id": "test-v1"},
                )
            if attempt < 2:
                await due(jobs, lost.id)
        result = await jobs.get(lost.id)
        assert result.status == "failed" and result.attempts == 3
        assert (await backups.history())["latest"]["status"] == "unknown"
        await jobs.recover()  # Terminal audit/notification not duplicated.
        async with transaction(database) as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "backup.unknown")
                )
                == 1
            )
    finally:
        await storage.close()


async def test_global_serialization_and_unverified_success_fails_closed(database):  # noqa: F811
    jobs, backups = services(database)
    result = await asyncio.gather(
        *[backups.request(None, "race", uuid4()) for _ in range(2)], return_exceptions=True
    )
    assert sum(isinstance(item, BackupBusy) for item in result) == 1
    job = next(item for item in result if isinstance(item, Job))
    claim = await jobs.claim(job.id)
    await jobs.complete(job.id, claim.claim_token)
    assert (await jobs.get(job.id)).status == "failed"
    assert (await backups.history())["latest"]["status"] == "unknown"
