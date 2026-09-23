"""Worker reconciles durable credential intent through a typed execution port."""

from cryptography.fernet import InvalidToken
from sqlalchemy import func, select
from sqlalchemy.orm import undefer

from apps.execution.ports import CredentialParameters, ExecutionRequest, Outcome, Target
from apps.jobs.worker import ExecutionFailure, Handler, RetryableError
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Device, DeviceCredential, Server, VpnUser


def credential_handlers(port, vpn):
    async def execute(context):
        await context.service.check_execution(context.job_id, context.token)
        job = await context.service.get(context.job_id)
        if job is None or job.target_type != "credential" or job.target_id is None:
            raise ExecutionFailure("execution_invalid")
        if vpn.cipher is None:
            raise ExecutionFailure("execution_invalid")
        # Serialize against all user/device/access mutations and other jobs for this
        # credential. The remote operation is bounded to 45 seconds; a lost worker
        # can replay the idempotent node helper after PostgreSQL lease recovery.
        async with transaction(vpn.engine) as db:
            credential = await db.scalar(
                select(DeviceCredential).where(DeviceCredential.id == job.target_id)
            )
            if credential is None:
                raise ExecutionFailure("execution_invalid")
            device = await db.scalar(select(Device).where(Device.id == credential.device_id))
            user = await db.scalar(
                select(VpnUser).where(VpnUser.id == device.user_id).with_for_update()
            )
            credential = await db.scalar(
                select(DeviceCredential)
                .where(DeviceCredential.id == job.target_id)
                .options(undefer(DeviceCredential.secret_ciphertext))
                .with_for_update()
            )
            if job.type == "credential.create":
                if credential.status == "active":
                    return
                if credential.status != "pending" or not user.enabled or not device.enabled:
                    return  # Revocation intent superseded this queued create.
                if user.expires_at and user.expires_at <= await db.scalar(
                    select(func.clock_timestamp())
                ):
                    return
                if user.access_mode == "selected":
                    from apps.persistence.models import ServerAccess

                    if await db.get(ServerAccess, (user.id, credential.server_id)) is None:
                        return
            elif job.type == "credential.revoke":
                if credential.status == "revoked":
                    return
                if credential.status != "revoking":
                    raise ExecutionFailure("execution_invalid")
            else:
                raise ExecutionFailure("execution_invalid")
            server = await db.scalar(
                select(Server).where(Server.id == credential.server_id).with_for_update()
            )
            if server is None or not server.ssh_host_key or not server.ssh_private_ciphertext:
                raise ExecutionFailure("execution_invalid")
            if job.type == "credential.create" and not server.enabled:
                return
            try:
                private = vpn.cipher.decrypt(server.ssh_private_ciphertext).decode()
                password = (
                    vpn.cipher.decrypt(credential.secret_ciphertext).decode()
                    if job.type == "credential.create"
                    else None
                )
                request = ExecutionRequest(
                    operation=job.type,
                    target=Target(
                        host=server.hostname,
                        user=server.ssh_user,
                        port=server.ssh_port,
                        host_key=server.ssh_host_key,
                        private_key=private,
                    ),
                    credential=CredentialParameters(
                        username=credential.username, password=password
                    ),
                )
            except (InvalidToken, ValueError, UnicodeError, TypeError):
                raise ExecutionFailure("execution_invalid") from None

            async def emit(event):
                await context.service.execution_event(context.job_id, context.token, event.value)

            result = await port.execute(request, emit)
            await context.service.check_execution(context.job_id, context.token)
            if result.outcome != Outcome.SUCCEEDED:
                # Node helper is idempotent; all transient failures are retryable.
                raise RetryableError
            now = await db.scalar(select(func.clock_timestamp()))
            credential.status = "active" if job.type == "credential.create" else "revoked"
            credential.applied_at = now
            if job.type == "credential.revoke":
                credential.secret_ciphertext = None
            db.add(
                AuditEvent(
                    actor_type="system",
                    actor_id=None,
                    action=job.type + ".applied",
                    target_type="credential",
                    target_id=credential.id,
                    request_id=job.request_id,
                    result="success",
                    details={"job_id": str(job.id)},
                )
            )

    return {
        "credential.create": Handler(execute, replay_safe=True, cancellable=False),
        "credential.revoke": Handler(execute, replay_safe=True, cancellable=False),
    }
