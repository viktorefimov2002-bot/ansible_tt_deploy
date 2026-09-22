from hashlib import sha256

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from apps.execution.ports import ExecutionRequest, PreflightParameters, Target
from apps.jobs.service import record
from apps.jobs.worker import ExecutionFailure
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Job, Server


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
                self.audit(db, actor, action, server, request_id, details)
                await db.flush()
                await db.refresh(server)
                return representation(server)
        except IntegrityError:
            raise ServerError(409, "Server name already exists") from None

    async def enqueue(self, server_id, operation, key, actor, request_id):
        # Server row serializes mutations and admission, including concurrent requests.
        async with transaction(self.engine) as db:
            server = await self.get_locked(db, server_id)
            identity = sha256(f"{actor}:{server_id}:{operation}:{key}".encode()).hexdigest()
            existing = await db.scalar(select(Job).where(Job.idempotency_key == identity))
            if existing:
                return existing
            await self.get_locked(db, server_id, idle=True)
            if not server.enabled or not server.ssh_private_ciphertext or not server.ssh_host_key:
                raise ServerError(409, "Server is disabled or SSH is not configured")
            job = Job(
                type=operation,
                target_type="server",
                target_id=server_id,
                created_by=actor,
                request_id=request_id,
                idempotency_key=identity,
                replay_safe=True,
                cancellable=True,
            )
            db.add(job)
            await db.flush()
            record(job, "queued", await db.scalar(select(func.now())))
            self.audit(db, actor, operation, server, request_id, {"job_id": str(job.id)})
            return job  # Normal durable dispatcher delivers after commit, even if Redis is down.

    async def resolve(self, target_id, operation="server.status"):
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
                        private_key=private,
                    ),
                    preflight=PreflightParameters(
                        domain=server.domain,
                        public_ip=str(server.public_ip),
                        acme_http=server.acme_http,
                    )
                    if operation == "server.preflight"
                    else None,
                )
            except (InvalidToken, ValueError, UnicodeError):
                raise ExecutionFailure("execution_invalid") from None
