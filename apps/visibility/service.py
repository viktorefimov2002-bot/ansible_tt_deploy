"""Audit-backed notifications survive transport loss without writes during reads."""

from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, NotificationRead

# Reviewed templates are the only notification text source. Audit metadata and
# free-form failures never enter this representation.
NOTIFICATION_TYPES = {
    ("backup.succeeded", "success", "job"): (
        "info",
        "Backup completed",
        "An encrypted off-host backup was uploaded and verified.",
    ),
    ("backup.failed", "failure", "job"): (
        "critical",
        "BACKUP_FAILED",
        "Backup failed. Inspect its safe job log and the backup runbook.",
    ),
    ("backup.unknown", "failure", "job"): (
        "critical",
        "BACKUP_FAILED",
        "Backup outcome is unknown. Verify the remote archive with the maintenance CLI.",
    ),
    ("alert.node_offline", "open", "server"): (
        "critical",
        "Node monitoring unavailable",
        "Managed-node collection has failed or remained stale. Check the node and management path.",
    ),
    ("alert.node_offline", "recovered", "server"): (
        "info",
        "Node monitoring recovered",
        "Fresh managed-node collection has resumed.",
    ),
    ("alert.service_down", "open", "server"): (
        "critical",
        "TrustTunnel service down or degraded",
        "The service or process is down, or the fixed service health probe is failing.",
    ),
    ("alert.service_down", "recovered", "server"): (
        "info",
        "TrustTunnel service recovered",
        "The fixed service probe confirms an active service and running process.",
    ),
    ("alert.disk_high", "open", "server"): (
        "warning",
        "Sustained high disk usage",
        "Observed filesystem usage has remained at or above 90% for five minutes.",
    ),
    ("alert.disk_high", "recovered", "server"): (
        "info",
        "Disk usage recovered",
        "Observed filesystem usage has remained at or below 85% for one minute.",
    ),
    ("alert.memory_high", "open", "server"): (
        "warning",
        "Sustained high memory usage",
        "Observed memory usage has remained at or above 90% for five minutes.",
    ),
    ("alert.memory_high", "recovered", "server"): (
        "info",
        "Memory usage recovered",
        "Observed memory usage has remained at or below 80% for one minute.",
    ),
    ("auth.login", "denied", "admin"): (
        "warning",
        "Sign-in failed",
        "An administrative sign-in attempt was denied.",
    ),
    ("auth.authorization", "denied", "admin"): (
        "warning",
        "Access denied",
        "An administrative API request was denied.",
    ),
    ("client.invitation.exchange", "denied", "invitation"): (
        "warning",
        "Invitation rejected",
        "A client invitation exchange was denied.",
    ),
    ("server.credentials.rotate", "success", "server"): (
        "warning",
        "Server SSH identity changed",
        "An administrator replaced server SSH credentials.",
    ),
    ("user.expire", "success", "vpn_user"): (
        "warning",
        "VPN user expired",
        "User access expired and credential revocation was requested.",
    ),
    ("user.disable", "success", "vpn_user"): (
        "info",
        "VPN user disabled",
        "An administrator disabled user access.",
    ),
    ("device.revoke", "success", "device"): (
        "info",
        "Device revoked",
        "Device access was revoked.",
    ),
    ("job.succeeded", "success", "job"): (
        "info",
        "Job completed",
        "An operational job completed successfully.",
    ),
    ("job.failed", "failure", "job"): (
        "critical",
        "Job failed",
        "An operational job failed. Inspect its safe job log for details.",
    ),
    ("job.cancel", "success", "job"): (
        "info",
        "Job cancellation requested",
        "An administrator requested safe job cancellation.",
    ),
}

AUDIT_FIELDS = (
    "id",
    "created_at",
    "actor_type",
    "actor_id",
    "action",
    "target_type",
    "target_id",
    "request_id",
    "result",
)


def notification_condition():
    return or_(
        *(
            and_(
                AuditEvent.action == action,
                AuditEvent.result == result,
                AuditEvent.target_type == target,
            )
            for action, result, target in NOTIFICATION_TYPES
        )
    )


def notification_view(event, read_at):
    severity, title, message = NOTIFICATION_TYPES[(event.action, event.result, event.target_type)]
    return {
        "id": event.id,
        "type": "BACKUP_FAILED"
        if event.action in ("backup.failed", "backup.unknown")
        else ("BACKUP_SUCCEEDED" if event.action == "backup.succeeded" else event.action),
        "created_at": event.created_at,
        "severity": severity,
        "title": title,
        "message": message,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "request_id": event.request_id,
        "read_at": read_at,
    }


class VisibilityService:
    def __init__(self, engine: AsyncEngine):
        self.engine = engine

    async def audit(self, *, offset=0, limit=50, since=None, until=None, **filters):
        conditions = [
            getattr(AuditEvent, field) == value
            for field, value in filters.items()
            if value is not None and field in AUDIT_FIELDS
        ]
        if since is not None:
            conditions.append(AuditEvent.created_at >= since)
        if until is not None:
            conditions.append(AuditEvent.created_at <= until)
        async with transaction(self.engine) as db:
            # Select only reviewed fields: metadata is never even loaded here.
            query = select(*(getattr(AuditEvent, field) for field in AUDIT_FIELDS)).where(
                *conditions
            )
            total = await db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
            rows = await db.execute(
                query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return {
                "items": [dict(row._mapping) for row in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    async def notifications(self, admin_id: UUID, *, offset=0, limit=50, unread_only=False):
        async with transaction(self.engine) as db:
            join = and_(
                NotificationRead.event_id == AuditEvent.id,
                NotificationRead.admin_id == admin_id,
            )
            conditions = [notification_condition()]
            count = select(func.count()).select_from(AuditEvent).outerjoin(NotificationRead, join)
            unread_count = await db.scalar(
                count.where(*conditions, NotificationRead.read_at.is_(None))
            )
            if unread_only:
                conditions.append(NotificationRead.read_at.is_(None))
            total = await db.scalar(count.where(*conditions))
            query = (
                select(
                    *(getattr(AuditEvent, field) for field in AUDIT_FIELDS),
                    NotificationRead.read_at,
                )
                .outerjoin(NotificationRead, join)
                .where(*conditions)
                .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
                .offset(offset)
                .limit(limit)
            )
            return {
                "items": [notification_view(row, row.read_at) for row in await db.execute(query)],
                "total": total,
                "unread_count": unread_count,
                "offset": offset,
                "limit": limit,
            }

    async def set_read(self, event_id: UUID, admin_id: UUID, read: bool):
        async with transaction(self.engine) as db:
            event = (
                await db.execute(
                    select(*(getattr(AuditEvent, field) for field in AUDIT_FIELDS)).where(
                        AuditEvent.id == event_id, notification_condition()
                    )
                )
            ).first()
            if event is None:
                return None
            if read:
                # Preserve the original read timestamp across retried requests.
                await db.execute(
                    insert(NotificationRead)
                    .values(admin_id=admin_id, event_id=event_id)
                    .on_conflict_do_nothing(
                        index_elements=[NotificationRead.admin_id, NotificationRead.event_id]
                    )
                )
            else:
                await db.execute(
                    delete(NotificationRead).where(
                        NotificationRead.admin_id == admin_id, NotificationRead.event_id == event_id
                    )
                )
            read_at = await db.scalar(
                select(NotificationRead.read_at).where(
                    NotificationRead.admin_id == admin_id, NotificationRead.event_id == event_id
                )
            )
            return notification_view(event, read_at)
