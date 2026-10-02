from datetime import timedelta
from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.execution.ports import (
    LIFECYCLE,
    ExecutionRequest,
    LifecycleParameters,
    PreflightParameters,
    Target,
)
from apps.jobs.service import record
from apps.jobs.worker import ExecutionFailure
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, DeviceCredential, Job, Server


class ServerError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def representation(server):
    return {
        name: getattr(server, name)
        for name in (
            "id",
            "name",
            "hostname",
            "public_ip",
            "domain",
            "location",
            "enabled",
            "status",
            "ssh_user",
            "ssh_port",
            "acme_http",
            "created_at",
            "updated_at",
            "last_seen_at",
            "trusttunnel_version",
            "desired_trusttunnel_version",
            "lifecycle_state",
            "lifecycle_job_id",
            "preflight_passed_at",
        )
    } | {
        "ssh_configured": server.ssh_private_ciphertext is not None,
        "host_key_fingerprint": fingerprint(server.ssh_host_key),
    }


def fingerprint(key):
    import base64

    return (
        (
            "SHA256:"
            + base64.b64encode(sha256(base64.b64decode(key.split()[1])).digest())
            .decode()
            .rstrip("=")
        )
        if key
        else None
    )


class ServerService:
    def __init__(self, engine, key):
        self.engine = engine
        self.cipher = Fernet(key.encode("ascii")) if key else None

    def encrypt(self, credentials):
        if self.cipher is None:
            raise ServerError(503, "Management encryption unavailable")
        return self.cipher.encrypt(credentials.private_key.get_secret_value().encode())

    async def get_locked(self, db, server_id, *, idle=False):
        server = await db.scalar(select(Server).where(Server.id == server_id).with_for_update())
        if server is None:
            raise ServerError(404, "Server not found")
        if idle and await db.scalar(
            select(Job.id)
            .where(
                Job.target_type == "server",
                Job.target_id == server_id,
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        ):
            raise ServerError(409, "Server has an active job")
        if idle and await db.scalar(
            select(Job.id)
            .join(DeviceCredential, Job.target_id == DeviceCredential.id)
            .where(
                Job.target_type == "credential",
                DeviceCredential.server_id == server_id,
                Job.status.in_(["queued", "running"]),
            )
            .limit(1)
        ):
            raise ServerError(409, "Server has an active credential job")
        return server

    def audit(self, db, actor, action, server, request_id, details=None):
        db.add(
            AuditEvent(
                actor_type="admin",
                actor_id=actor,
                action=action,
                target_type="server",
                target_id=server.id,
                request_id=request_id,
                result="success",
                details=details or {},
            )
        )

    async def list(self, offset=0, limit=100):
        async with transaction(self.engine) as db:
            return [
                representation(s)
                for s in (
                    await db.scalars(select(Server).order_by(Server.id).offset(offset).limit(limit))
                ).all()
            ]

    async def get(self, server_id):
        async with transaction(self.engine) as db:
            server = await db.get(Server, server_id)
            if server is None:
                raise ServerError(404, "Server not found")
            return representation(server)

    async def create(self, body, actor, request_id):
        try:
            async with transaction(self.engine) as db:
                server = Server(
                    **body.model_dump(),
                    ssh_host_key=body.credentials.host_key,
                    ssh_private_ciphertext=self.encrypt(body.credentials),
                )
                db.add(server)
                await db.flush()
                self.audit(db, actor, "server.create", server, request_id)
                return representation(server)
        except IntegrityError:
            raise ServerError(409, "Server name already exists") from None

    async def update(self, server_id, body, actor, request_id, action="server.update"):
        try:
            async with transaction(self.engine) as db:
                server = await self.get_locked(db, server_id, idle=True)
                details = {}
                if action == "server.credentials.rotate":
                    details = {
                        "old_fingerprint": fingerprint(server.ssh_host_key),
                        "new_fingerprint": fingerprint(body.host_key),
                    }
                    server.ssh_private_ciphertext = self.encrypt(body)
                    server.ssh_host_key = body.host_key
                else:
                    for name, value in body.model_dump().items():
                        setattr(server, name, value)
                server.status = "pending" if server.enabled else "disabled"
                server.last_seen_at = None
                server.preflight_passed_at = None
                self.audit(db, actor, action, server, request_id, details)
                await db.flush()
                await db.refresh(server)
                return representation(server)
        except IntegrityError:
            raise ServerError(409, "Server name already exists") from None

    async def enqueue(self, server_id, operation, key, actor, request_id, parameters=None):
        if operation not in ("server.status", "server.preflight", *LIFECYCLE):
            raise ServerError(422, "Unsupported server operation")
        parameters = parameters or {}
        # Server row serializes mutations and admission, including concurrent requests.
        async with transaction(self.engine) as db:
            server = await self.get_locked(db, server_id)
            identity = sha256(f"{actor}:{server_id}:{operation}:{key}".encode()).hexdigest()
            existing = await db.scalar(select(Job).where(Job.idempotency_key == identity))
            if existing:
                if existing.parameters != parameters:
                    raise ServerError(409, "Idempotency key belongs to different parameters")
                return existing
            await self.get_locked(db, server_id, idle=True)
            if not server.enabled or not server.ssh_private_ciphertext or not server.ssh_host_key:
                raise ServerError(409, "Server is disabled or SSH is not configured")
            now = await db.scalar(select(func.clock_timestamp()))
            if operation == "server.deploy":
                if not server.preflight_passed_at or now - server.preflight_passed_at > timedelta(
                    minutes=15
                ):
                    raise ServerError(409, "Run and pass pre-flight checks before deployment")
                if server.trusttunnel_version:
                    raise ServerError(409, "Use update for an installed server")
            if operation in ("server.update", "server.restart") and not server.trusttunnel_version:
                raise ServerError(409, "Server has no confirmed installed workload")
            if operation == "server.uninstall" and await db.scalar(
                select(DeviceCredential.id)
                .where(
                    DeviceCredential.server_id == server_id,
                    DeviceCredential.status.in_(["pending", "active", "revoking"]),
                )
                .limit(1)
            ):
                raise ServerError(409, "Revoke server credentials before uninstalling")
            if operation in LIFECYCLE:
                # Validate the complete typed intent before storing it, without secrets.
                try:
                    intent = self.lifecycle_parameters(server, operation, parameters)
                    if operation == "server.deploy" and server.acme_http and not intent.acme_email:
                        raise ValueError
                except ValueError:
                    raise ServerError(422, "Invalid lifecycle parameters") from None
            job = Job(
                type=operation,
                target_type="server",
                target_id=server_id,
                created_by=actor,
                request_id=request_id,
                idempotency_key=identity,
                replay_safe=operation != "server.restart",
                cancellable=operation not in LIFECYCLE,
                parameters=parameters,
            )
            db.add(job)
            await db.flush()
            record(job, "queued", await db.scalar(select(func.now())))
            if operation == "server.preflight":
                server.preflight_passed_at = None
            if operation in LIFECYCLE:
                server.lifecycle_job_id = job.id
                server.lifecycle_state = operation.removeprefix("server.") + "_pending"
                if operation in ("server.deploy", "server.update"):
                    server.desired_trusttunnel_version = parameters["version"]
            self.audit(db, actor, operation, server, request_id, {"job_id": str(job.id)})
            return job  # Normal durable dispatcher delivers after commit, even if Redis is down.

    @staticmethod
    def lifecycle_parameters(server, operation, parameters):
        allowed = (
            {"version", "acme_email"}
            if operation == "server.deploy"
            else ({"version"} if operation == "server.update" else set())
        )
        if set(parameters) - allowed:
            raise ValueError("Unsupported lifecycle parameters")
        intent = LifecycleParameters.model_validate(
            parameters
            | (
                {"domain": server.domain, "acme_http": server.acme_http}
                if operation == "server.deploy"
                else {}
            )
        )

        if (operation in ("server.deploy", "server.update")) != (intent.version is not None):
            raise ValueError("Lifecycle operation requires matching version intent")
        return intent

    async def resolve(self, target_id, operation="server.status", parameters=None):
        async with transaction(self.engine) as db:
            server = await db.get(Server, target_id)
            if (
                server is None
                or not server.enabled
                or not server.ssh_host_key
                or not server.ssh_private_ciphertext
                or self.cipher is None
            ):
                raise ExecutionFailure("execution_invalid")
            try:
                private = self.cipher.decrypt(server.ssh_private_ciphertext).decode()
                return ExecutionRequest(
                    operation=operation,
                    target=Target(
                        host=server.hostname,
                        user=server.ssh_user,
                        port=server.ssh_port,
                        host_key=server.ssh_host_key,
                        private_key=SecretStr(private),
                    ),
                    preflight=PreflightParameters(
                        domain=server.domain,
                        public_ip=str(server.public_ip),
                        acme_http=server.acme_http,
                    )
                    if operation == "server.preflight"
                    else None,
                    lifecycle=self.lifecycle_parameters(server, operation, parameters or {})
                    if operation in LIFECYCLE
                    else None,
                    timeout_seconds=600 if operation in LIFECYCLE else 45,
                )
            except (InvalidToken, ValueError, UnicodeError):
                raise ExecutionFailure("execution_invalid") from None
