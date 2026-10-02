"""PostgreSQL is authoritative, including when every Redis key has disappeared."""

from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.jobs.ports import EventPublisher, JobQueue, TransportUnavailable
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Job, Server, ServerConfigRevision

TERMINAL = {"succeeded", "failed", "cancelled"}
MESSAGES = {
    "queued": "Job queued",
    "running": "Attempt started",
    "checkpoint": "Handler checkpoint",
    "succeeded": "Job completed",
    "cancel_requested": "Safe cancellation requested",
    "cancelled": "Job cancelled at a safe boundary",
    "retry": "Retry scheduled",
    "handler_failed": "Handler failed",
    "timeout": "Attempt timed out",
    "worker_lost": "Worker lease expired",
    "shutdown": "Worker stopped during execution",
    "unknown_handler": "Handler is unavailable or its policy changed",
    "unsafe_outcome": "Execution outcome requires reconciliation",
    "execution_started": "Execution started",
    "execution_task_ok": "Remote task completed",
    "execution_task_failed": "Remote task failed",
    "execution_task_skipped": "Remote task skipped",
    "execution_output_suppressed": "Unstructured process output suppressed",
    "execution_failed": "Execution process failed",
    "execution_unreachable": "Execution target unreachable",
    "execution_timeout": "Execution deadline exceeded; local processes stopped",
    "execution_unavailable": "Execution runtime unavailable",
    "execution_invalid": "Execution input or target is invalid",
    "config_rolled_back": "Configuration apply failed; previous configuration restored and healthy",
    "config_rollback_failed": (
        "Configuration apply and rollback failed; server health requires attention"
    ),
}

EXECUTION_EVENTS = frozenset(
    {
        "execution_started",
        "execution_task_ok",
        "execution_task_failed",
        "execution_task_skipped",
        "execution_output_suppressed",
        "execution_unreachable",
    }
)
EXECUTION_FAILURES = frozenset(
    {
        "execution_failed",
        "execution_unreachable",
        "execution_timeout",
        "execution_unavailable",
        "execution_invalid",
        "config_rolled_back",
        "config_rollback_failed",
    }
)


class LostClaim(Exception):
    pass


class CancellationRequested(Exception):
    pass


class CancellationUnsafe(Exception):
    pass


def record(job: Job, code: str, now) -> dict:
    # Deliberately no free-text/raw exception logging API. Add reviewed event codes
    # alongside future adapters instead of copying remote output or secrets here.
    job.log_sequence += 1
    event = dict(
        sequence=job.log_sequence,
        code=code,
        message=MESSAGES[code],
        at=now.isoformat(),
        attempt=job.attempts,
        progress=job.progress,
    )
    job.history = [*job.history[-199:], event]
    return event


def finish(job: Job, status: str, now):
    job.status = status
    job.finished_at = now
    job.claim_token = None
    job.lease_until = None


class JobService:
    def __init__(self, engine: AsyncEngine, queue: JobQueue, events: EventPublisher):
        self.engine, self.queue, self.events = engine, queue, events

    async def publish(self, job_id: UUID, event: dict):
        try:
            await self.events.publish(job_id, event)
        except TransportUnavailable:
            pass  # Already committed; SSE can repair from durable history.

    async def create(
        self,
        *,
        kind: str,
        target_type: str,
        target_id: UUID | None,
        created_by: UUID | None,
        request_id: str,
        idempotency_key: str,
        replay_safe: bool = False,
        cancellable: bool = False,
        max_attempts: int = 3,
    ) -> Job:
        """Internal only. Future business services must authorize before calling.

        For atomic business mutations, insert Job in their own transaction instead;
        dispatch() finds it after commit. Never enqueue before committing intent.
        """
        async with transaction(self.engine) as db:
            values = dict(
                type=kind,
                target_type=target_type,
                target_id=target_id,
                created_by=created_by,
                request_id=request_id,
                idempotency_key=idempotency_key,
                replay_safe=replay_safe,
                cancellable=cancellable,
                max_attempts=max_attempts,
            )
            job_id = await db.scalar(
                insert(Job)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[Job.idempotency_key])
                .returning(Job.id)
            )
            job = await db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
            assert job is not None
            for field in (
                "type",
                "target_type",
                "target_id",
                "created_by",
                "replay_safe",
                "cancellable",
                "max_attempts",
            ):
                if getattr(job, field) != values[field]:
                    raise ValueError("Idempotency key belongs to a different operation")
            if job_id:
                record(job, "queued", await db.scalar(select(func.now())))
        if job_id:
            await self.publish(job.id, job.history[-1])
            try:
                await self.queue.enqueue(job.id)
            except TransportUnavailable:
                pass
        return job

    async def get(self, job_id: UUID) -> Job | None:
        async with transaction(self.engine) as db:
            return await db.get(Job, job_id)

    async def list(self, offset: int = 0, limit: int = 50) -> list[Job]:
        async with transaction(self.engine) as db:
            return list(
                (
                    await db.scalars(
                        select(Job)
                        .order_by(Job.created_at.desc(), Job.id.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
            )

    async def dispatch(self):
        # Enqueue under a short row lock. Redis delivery is only a hint; the intent
        # predates it and a rollback/crash can only cause duplicate delivery.
        async with transaction(self.engine) as db:
            now = await db.scalar(select(func.now()))
            jobs = (
                await db.scalars(
                    select(Job)
                    .where(Job.status == "queued", Job.available_at <= now, Job.dispatch_at <= now)
                    .order_by(Job.dispatch_at, Job.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for job in jobs:
                await self.queue.enqueue(job.id)
                job.dispatch_at = now + timedelta(seconds=5)

    async def claim(self, job_id: UUID, lease_seconds: float = 90) -> Job | None:
        async with transaction(self.engine) as db:
            job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            now = await db.scalar(select(func.clock_timestamp()))
            if job is None or job.status != "queued" or job.available_at > now:
                return None
            if job.attempts >= job.max_attempts:
                return None
            job.status = "running"
            job.attempts += 1
            job.started_at = job.started_at or now
            job.claim_token = uuid4()
            job.lease_until = now + timedelta(seconds=lease_seconds)
            event = record(job, "running", now)
        await self.publish(job.id, event)
        return job

    async def checkpoint(self, job_id: UUID, token: UUID, progress: int):
        if not 0 <= progress <= 100:
            raise ValueError("Invalid progress")
        async with transaction(self.engine) as db:
            job, now = await self._owned(db, job_id, token)
            if job.cancel_requested_at:
                raise CancellationRequested
            job.progress = progress
            event = record(job, "checkpoint", now)
        await self.publish(job_id, event)

    async def _owned(self, db, job_id, token):
        job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        now = await db.scalar(select(func.clock_timestamp()))
        if (
            job is None
            or job.status != "running"
            or job.claim_token != token
            or job.lease_until <= now
        ):
            raise LostClaim
        return job, now

    async def execution_event(self, job_id: UUID, token: UUID, code: str):
        if code not in EXECUTION_EVENTS:
            raise ValueError("Unsupported execution event")
        async with transaction(self.engine) as db:
            job, now = await self._owned(db, job_id, token)
            if job.cancel_requested_at:
                raise CancellationRequested
            event = record(job, code, now)
        await self.publish(job_id, event)

    async def check_execution(self, job_id: UUID, token: UUID):
        """Fence/cancel a silent subprocess without filling the log with polling."""
        async with transaction(self.engine) as db:
            job, _ = await self._owned(db, job_id, token)
            if job.cancel_requested_at:
                raise CancellationRequested

    async def execution_result(self, job_id, token, result):
        from apps.execution.ports import CHECK_STATES, CHECKS, LIFECYCLE, Outcome, preflight_ready

        outcome = Outcome(result.outcome)
        checks = result.checks
        if checks is not None and (
            set(checks) != set(CHECKS)
            or any(value not in CHECK_STATES for value in checks.values())
        ):
            raise ValueError("Invalid diagnostic result")
        async with transaction(self.engine) as db:
            job, now = await self._owned(db, job_id, token)
            if job.cancel_requested_at:
                raise CancellationRequested
            job.result = {"outcome": outcome.value, "attempt": job.attempts}
            if job.type == "server.config.apply":
                from apps.servers.configuration import validated_config_result

                if result.config_apply is not None:
                    report = validated_config_result(job.parameters, result.config_apply)
                    if outcome != Outcome.SUCCEEDED and report["state"] == "applied":
                        raise ValueError("Applied configuration requires successful execution")
                    job.result["config_apply"] = report
                return  # Revision and current pointer commit with terminal completion.
            if job.type in LIFECYCLE:
                from apps.execution.lifecycle import validated_result

                if outcome == Outcome.SUCCEEDED:
                    job.result["lifecycle"] = validated_result(
                        job.type, job.parameters, result.lifecycle
                    )
                return  # Workload state commits with terminal job completion below.
            if checks is not None:
                job.result = job.result | {
                    "checks": checks,
                    "ready": all(value in ("pass", "skipped") for value in checks.values()),
                }
            server = await db.get(Server, job.target_id)
            if server is not None:
                if job.type == "server.preflight":
                    ready = outcome == Outcome.SUCCEEDED and preflight_ready(
                        checks, server.acme_http
                    )
                    job.result["ready"] = ready
                    server.preflight_passed_at = now if ready else None
                reachable = checks.get("ssh") == "pass" if checks else outcome == Outcome.SUCCEEDED
                server.status = "reachable" if reachable else "unknown"
                if reachable:
                    server.last_seen_at = now

    def _terminal_audit(self, db, job):
        if job.status not in ("succeeded", "failed"):
            return
        db.add(
            AuditEvent(
                actor_type="system",
                action="job." + job.status,
                target_type="job",
                target_id=job.id,
                request_id=job.request_id,
                result="success" if job.status == "succeeded" else "failure",
            )
        )

    async def _lifecycle_terminal(self, db, job, now):
        from apps.execution.ports import LIFECYCLE

        if job.type == "server.config.apply":
            await self._config_terminal(db, job, now)
            return
        if job.type not in LIFECYCLE or job.status not in TERMINAL:
            return
        server = await db.scalar(select(Server).where(Server.id == job.target_id).with_for_update())
        if server is None or server.lifecycle_job_id != job.id:
            return
        if job.status == "succeeded":
            from apps.execution.lifecycle import validated_result

            report = validated_result(job.type, job.parameters, (job.result or {}).get("lifecycle"))
            server.trusttunnel_version = report["installed_version"]
            server.lifecycle_state = "installed" if server.trusttunnel_version else "uninstalled"
            server.last_seen_at = now
            server.status = "reachable"
            if job.type == "server.uninstall":
                server.desired_trusttunnel_version = None
                server.config_revision_id = None
                server.config_state = "idle"
                server.config_job_id = None
            if job.type in ("server.deploy", "server.uninstall"):
                server.preflight_passed_at = None
        elif job.status == "cancelled" and job.attempts == 0:
            server.lifecycle_state = "installed" if server.trusttunnel_version else "unknown"
            server.desired_trusttunnel_version = server.trusttunnel_version
        else:
            server.lifecycle_state = (
                "unknown"
                if job.error_code
                in ("unsafe_outcome", "worker_lost", "timeout", "shutdown", "execution_timeout")
                or job.status == "cancelled"
                else "failed"
            )
        db.add(
            AuditEvent(
                actor_type="system",
                action=job.type + "." + job.status,
                target_type="server",
                target_id=server.id,
                request_id=job.request_id,
                result="success" if job.status == "succeeded" else "failure",
                details={"job_id": str(job.id)},
            )
        )

    async def _config_terminal(self, db, job, now):
        if job.status not in TERMINAL:
            return
        server = await db.scalar(select(Server).where(Server.id == job.target_id).with_for_update())
        if server is None or server.config_job_id != job.id:
            return
        revision = await db.scalar(
            select(ServerConfigRevision).where(
                ServerConfigRevision.server_id == server.id,
                ServerConfigRevision.id == UUID(job.parameters["revision_id"]),
                ServerConfigRevision.job_id == job.id,
            )
        )
        if revision is None:
            return
        from apps.servers.configuration import validated_config_result

        report = (job.result or {}).get("config_apply")
        if report is not None:
            report = validated_config_result(job.parameters, report)
        if job.status == "succeeded":
            if report is None or report["state"] != "applied":
                raise ValueError("Configuration activation is not confirmed")
            server.config_revision_id = revision.id
            server.config_state = revision.status = "applied"
            server.status = "reachable"
            server.last_seen_at = revision.applied_at = now
            revision.failure_code = revision.failure_message = None
        elif report is not None and report["state"] in ("rolled_back", "rollback_failed"):
            server.config_state = revision.status = report["state"]
            revision.failure_code = "config_" + report["state"]
            revision.failure_message = MESSAGES[revision.failure_code]
            server.status = "reachable" if report["active"] else "unknown"
            if report["active"]:
                server.last_seen_at = now
        else:
            # Without a validated receipt an attempted mutation may have completed
            # remotely before its transport or worker failed. Keep the last
            # confirmed pointer and report uncertainty, including retry exhaustion.
            uncertain = job.attempts > 0
            revision.status = "unknown" if uncertain else "failed"
            server.config_state = revision.status
            if job.status == "cancelled" and job.attempts == 0:
                server.config_state = revision.previous_config_state or "unknown"
            revision.failure_code = job.error_code or "cancelled"
            revision.failure_message = MESSAGES[revision.failure_code]
            if uncertain:
                server.status = "unknown"
        db.add(
            AuditEvent(
                actor_type="system",
                action=job.type + "." + job.status,
                target_type="server",
                target_id=server.id,
                request_id=job.request_id,
                result="success" if job.status == "succeeded" else "failure",
                details={
                    "job_id": str(job.id),
                    "revision_id": str(revision.id),
                    "config_state": server.config_state,
                },
            )
        )

    def _failure(self, db, job, now, code, retryable, *, acknowledge_cancel=True):
        if job.type == "server.config.apply":
            report = (job.result or {}).get("config_apply")
            if report and report.get("state") in ("rolled_back", "rollback_failed"):
                code = "config_" + report["state"]
                retryable = False
        job.error_code, job.error_message = code, MESSAGES[code]
        if acknowledge_cancel and job.cancel_requested_at and job.cancellable:
            finish(job, "cancelled", now)
            record(job, "cancelled", now)
        elif retryable and job.replay_safe and job.attempts < job.max_attempts:
            job.status = "queued"
            job.claim_token = job.lease_until = None
            job.available_at = now + timedelta(seconds=min(2**job.attempts, 60))
            job.dispatch_at = job.available_at
            job.progress = 0
            record(job, code, now)
            record(job, "retry", now)
        else:
            finish(job, "failed", now)
            record(job, code, now)
            self._terminal_audit(db, job)

    async def complete(
        self, job_id: UUID, token: UUID, *, code: str | None = None, retryable: bool = False
    ):
        async with transaction(self.engine) as db:
            job, now = await self._owned(db, job_id, token)
            if code:
                self._failure(db, job, now, code, retryable)
            else:
                cancelled = job.cancel_requested_at is not None and job.cancellable
                finish(job, "cancelled" if cancelled else "succeeded", now)
                job.progress = job.progress if cancelled else 100
                job.error_code = job.error_message = None
                record(job, job.status, now)
                self._terminal_audit(db, job)
            events = job.history[-2:]
            await self._lifecycle_terminal(db, job, now)
        for event in events:
            await self.publish(job_id, event)

    async def recover(self):
        async with transaction(self.engine) as db:
            now = await db.scalar(select(func.clock_timestamp()))
            jobs = (
                await db.scalars(
                    select(Job)
                    .where(Job.status == "running", Job.lease_until <= now)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for job in jobs:
                # A lost worker cannot acknowledge reaching a safe checkpoint.
                retryable = job.replay_safe and job.cancel_requested_at is None
                self._failure(
                    db,
                    job,
                    now,
                    "worker_lost" if retryable else "unsafe_outcome",
                    retryable,
                    acknowledge_cancel=False,
                )
                await self._lifecycle_terminal(db, job, now)
        # No publish required for recovery: the API repairs the durable stream.

    async def cancel(self, job_id: UUID, actor: UUID, request_id: str) -> Job | None:
        async with transaction(self.engine) as db:
            job = await db.scalar(select(Job).where(Job.id == job_id).with_for_update())
            if job is None:
                return None
            if job.status in TERMINAL or job.cancel_requested_at:
                return job
            if job.status == "running" and not job.cancellable:
                raise CancellationUnsafe
            now = await db.scalar(select(func.clock_timestamp()))
            job.cancel_requested_at = now
            if job.status == "queued":
                finish(job, "cancelled", now)
                await self._lifecycle_terminal(db, job, now)
            event = record(
                job, "cancelled" if job.status == "cancelled" else "cancel_requested", now
            )
            db.add(
                AuditEvent(
                    actor_type="admin",
                    actor_id=actor,
                    action="job.cancel",
                    target_type="job",
                    target_id=job.id,
                    request_id=request_id,
                    result="success",
                    details={"status": job.status},
                )
            )
        await self.publish(job_id, event)
        return job
