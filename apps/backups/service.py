"""Backup intent/history uses existing PostgreSQL jobs, not Redis metadata."""

import tempfile
from uuid import uuid4

from sqlalchemy import func, select, text

from apps.backups.archive import HEADER, verify_archive
from apps.backups.config import BackupFailure
from apps.backups.storage import digest
from apps.jobs.service import TERMINAL, JobService, record
from apps.jobs.worker import Context, Handler
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Job


def backup_state(job):
    if job.status != "failed":
        return job.status
    if (job.result or {}).get("phase") in (
        "uploading",
        "verified",
    ) or job.error_code == "backup_unknown":
        return "unknown"
    return "failed"


def view(job):
    return {
        "id": job.id,
        "status": backup_state(job),
        "job_status": job.status,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "error_code": job.error_code,
    }


class BackupBusy(Exception):
    pass


class BackupService:
    def __init__(self, jobs: JobService, *, enabled=False):
        self.jobs, self.enabled = jobs, enabled

    async def request(self, actor, request_id, idempotency_key):
        if not self.enabled:
            raise BackupFailure("backup_unavailable")
        async with transaction(self.jobs.engine) as db:
            # Serializes the small global backup workload and duplicate requests.
            await db.execute(text("SELECT pg_advisory_xact_lock(2020020)"))
            identity = "backup:" + str(idempotency_key)
            existing = await db.scalar(select(Job).where(Job.idempotency_key == identity))
            if existing:
                if existing.type != "backup.run" or existing.created_by != actor:
                    raise BackupBusy()
                return existing
            active = await db.scalar(
                select(Job.id)
                .where(Job.type == "backup.run", Job.status.in_(("queued", "running")))
                .limit(1)
            )
            if active:
                raise BackupBusy()
            job = Job(
                id=uuid4(),
                type="backup.run",
                target_type="backup",
                created_by=actor,
                request_id=request_id,
                idempotency_key=identity,
                replay_safe=True,
                cancellable=False,
                max_attempts=3,
                history=[],
                log_sequence=0,
                attempts=0,
                progress=0,
            )
            db.add(job)
            now = await db.scalar(select(func.clock_timestamp()))
            record(job, "queued", now)
            db.add(
                AuditEvent(
                    actor_type="admin" if actor else "system",
                    actor_id=actor,
                    action="backup.run",
                    target_type="job",
                    target_id=job.id,
                    request_id=request_id,
                    result="success",
                )
            )
            await db.flush()
        # Durable outbox dispatcher reconstructs hints after outages/restarts.
        return job

    async def history(self, offset=0, limit=50):
        async with transaction(self.jobs.engine) as db:
            condition = Job.type == "backup.run"
            now = await db.scalar(select(func.clock_timestamp()))
            total = await db.scalar(select(func.count()).select_from(Job).where(condition))
            rows = (
                await db.scalars(
                    select(Job)
                    .where(condition)
                    .order_by(Job.created_at.desc(), Job.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            latest = await db.scalar(
                select(Job).where(condition).order_by(Job.created_at.desc(), Job.id.desc()).limit(1)
            )
            success = await db.scalar(
                select(Job)
                .where(condition, Job.status == "succeeded")
                .order_by(Job.finished_at.desc(), Job.id.desc())
                .limit(1)
            )
            return {
                "enabled": self.enabled,
                "items": [view(j) for j in rows],
                "offset": offset,
                "limit": limit,
                "total": total,
                "latest": view(latest) if latest else None,
                "last_success": view(success) if success else None,
                # Conservative age of the snapshot rather than upload completion.
                "age_seconds": max(0, int((now - success.started_at).total_seconds()))
                if success
                else None,
            }

    async def phase(self, context, phase, receipt=None):
        await self.jobs.backup_result(
            context.job_id, context.token, {"phase": phase, **(receipt or {})}
        )

    def handler(self, config, storage, tools):
        async def execute(context: Context):
            if config is None or storage is None:
                raise BackupFailure("backup_unavailable")
            # Temporary files contain ciphertext only. No plaintext spool, even on failure.
            with tempfile.TemporaryFile(prefix="ttcp-encrypted-") as encrypted:
                if not await storage.download(context.job_id, encrypted):
                    await self.phase(context, "dumping")
                    await tools.dump(encrypted, config, context.job_id)
                    await context.checkpoint(60)
                    await self.phase(context, "uploading")
                    await storage.put_if_absent(context.job_id, encrypted)
                    # Readback proves remote durability and authenticates conditional-write winners.
                    encrypted.seek(0)
                    encrypted.truncate()
                    if not await storage.download(context.job_id, encrypted):
                        raise BackupFailure("backup_unknown", retryable=True)
                await verify_archive(encrypted, config, context.job_id)
                sha = digest(encrypted)
                encrypted.seek(0, 2)
                size = encrypted.tell()
                encrypted.seek(0)
                key_id = HEADER.unpack(encrypted.read(HEADER.size))[2].rstrip(b"\0").decode()
                await self.phase(
                    context,
                    "verified",
                    {
                        "sha256": sha,
                        "encrypted_bytes": size,
                        "key_id": key_id,
                    },
                )
                await context.checkpoint(95)

        return Handler(
            execute,
            replay_safe=True,
            cancellable=False,
            timeout_seconds=config.timeout_seconds if config else 600,
        )


def terminal_audit(db, job):
    if job.status not in TERMINAL:
        return
    state = backup_state(job)
    db.add(
        AuditEvent(
            actor_type="system",
            action="backup." + state,
            target_type="job",
            target_id=job.id,
            request_id=job.request_id,
            result="success" if state == "succeeded" else "failure",
            details={
                "backup_status": state,
                "requested_by": str(job.created_by) if job.created_by else None,
            },
        )
    )
