"""Durable VPN identity intent; PostgreSQL rows serialize quota and revocation."""

import secrets
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, func, select
from sqlalchemy.orm import undefer

from apps.jobs.service import record
from apps.persistence.database import transaction
from apps.persistence.models import (
    AuditEvent,
    Device,
    DeviceCredential,
    Job,
    Server,
    ServerAccess,
    VpnUser,
)


class VpnError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


def user_view(user, servers=()):
    return {
        "id": user.id,
        "display_name": user.display_name,
        "enabled": user.enabled,
        "expires_at": user.expires_at,
        "device_limit": user.device_limit,
        "access_mode": user.access_mode,
        "server_ids": list(servers),
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def device_view(device):
    return {
        "id": device.id,
        "user_id": device.user_id,
        "name": device.name,
        "platform": device.platform,
        "enabled": device.enabled,
        "revoked_at": device.revoked_at,
        "created_at": device.created_at,
    }


def credential_view(credential):
    return {
        "id": credential.id,
        "device_id": credential.device_id,
        "server_id": credential.server_id,
        "username": credential.username,
        "status": credential.status,
        "applied_at": credential.applied_at,
        "revoked_at": credential.revoked_at,
        "download_available": credential.status == "active" and credential.downloaded_at is None,
    }


class VpnService:
    def __init__(self, engine, key):
        self.engine = engine
        self.cipher = Fernet(key.encode("ascii")) if key else None

    def audit(self, db, actor, action, target_type, target_id, request_id, details=None):
        db.add(
            AuditEvent(
                actor_type="admin" if actor else "system",
                actor_id=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                request_id=request_id,
                result="success",
                details=details or {},
            )
        )

    async def _user(self, db, user_id):
        user = await db.scalar(select(VpnUser).where(VpnUser.id == user_id).with_for_update())
        if user is None:
            raise VpnError(404, "VPN user not found")
        return user

    async def _view(self, db, user):
        selected = (
            await db.scalars(select(ServerAccess.server_id).where(ServerAccess.user_id == user.id))
        ).all()
        return user_view(user, selected)

    async def list_users(self):
        async with transaction(self.engine) as db:
            users = (await db.scalars(select(VpnUser).order_by(VpnUser.created_at))).all()
            return [await self._view(db, user) for user in users]

    async def get_user(self, user_id):
        async with transaction(self.engine) as db:
            user = await db.get(VpnUser, user_id)
            if user is None:
                raise VpnError(404, "VPN user not found")
            return await self._view(db, user)

    async def create_user(self, body, actor, request_id):
        async with transaction(self.engine) as db:
            user = VpnUser(**body.model_dump())
            db.add(user)
            await db.flush()
            self.audit(db, actor, "user.create", "vpn_user", user.id, request_id)
            return await self._view(db, user)

    async def update_user(self, user_id, body, actor, request_id):
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            if body.device_limit is not None:
                active_devices = await db.scalar(
                    select(func.count())
                    .select_from(Device)
                    .where(Device.user_id == user_id, Device.enabled)
                )
                if body.device_limit < active_devices:
                    raise VpnError(409, "Device quota is below active device count")
            for name, value in body.model_dump(exclude_unset=True).items():
                setattr(user, name, value)
            if user.expires_at and user.expires_at <= datetime.now(UTC):
                user.enabled = False
                await self._revoke_user(db, user, actor, request_id)
                self.audit(db, actor, "user.expire", "vpn_user", user.id, request_id)
            self.audit(db, actor, "user.update", "vpn_user", user.id, request_id)
            await db.flush()
            return await self._view(db, user)

    async def set_enabled(self, user_id, enabled, actor, request_id):
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            if enabled and user.expires_at and user.expires_at <= datetime.now(UTC):
                raise VpnError(409, "VPN user has expired")
            if user.enabled != enabled:
                user.enabled = enabled
                if not enabled:
                    await self._revoke_user(db, user, actor, request_id)
                self.audit(
                    db,
                    actor,
                    "user.enable" if enabled else "user.disable",
                    "vpn_user",
                    user.id,
                    request_id,
                )
            elif not enabled:
                await self._revoke_user(db, user, actor, request_id)
            await db.flush()
            return await self._view(db, user)

    async def set_access(self, user_id, body, actor, request_id):
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            ids = set(body.server_ids)
            found = set(
                (
                    await db.scalars(select(Server.id).where(Server.id.in_(ids), Server.enabled))
                ).all()
            )
            if found != ids:
                raise VpnError(422, "Selected server unavailable")
            previous = set(
                (
                    await db.scalars(
                        select(ServerAccess.server_id).where(ServerAccess.user_id == user_id)
                    )
                ).all()
            )
            if body.access_mode == "selected":
                removed = set() if user.access_mode == "all" else previous - ids
                if user.access_mode == "all":
                    # Include credentials on servers disabled since issuance.
                    removed = (
                        set(
                            (
                                await db.scalars(
                                    select(DeviceCredential.server_id)
                                    .join(Device, Device.id == DeviceCredential.device_id)
                                    .where(Device.user_id == user_id)
                                )
                            ).all()
                        )
                        - ids
                    )
            else:
                removed = set()
            user.access_mode = body.access_mode
            await db.execute(delete(ServerAccess).where(ServerAccess.user_id == user_id))
            db.add_all(ServerAccess(user_id=user_id, server_id=sid) for sid in ids)
            if removed:
                credentials = (
                    await db.scalars(
                        select(DeviceCredential)
                        .join(Device, Device.id == DeviceCredential.device_id)
                        .where(Device.user_id == user_id, DeviceCredential.server_id.in_(removed))
                        .with_for_update()
                    )
                ).all()
                for credential in credentials:
                    await self._revoke_credential(db, credential, actor, request_id)
            self.audit(db, actor, "user.access.update", "vpn_user", user.id, request_id)
            await db.flush()
            return await self._view(db, user)

    async def list_devices(self, user_id):
        async with transaction(self.engine) as db:
            if await db.get(VpnUser, user_id) is None:
                raise VpnError(404, "VPN user not found")
            return [
                device_view(device)
                for device in (
                    await db.scalars(
                        select(Device).where(Device.user_id == user_id).order_by(Device.created_at)
                    )
                ).all()
            ]

    async def create_device(self, user_id, body, actor, request_id):
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            self._require_active(user)
            count = await db.scalar(
                select(func.count())
                .select_from(Device)
                .where(Device.user_id == user_id, Device.enabled)
            )
            if count >= user.device_limit:
                raise VpnError(409, "Device quota reached")
            device = Device(user_id=user_id, **body.model_dump())
            db.add(device)
            await db.flush()
            self.audit(db, actor, "device.create", "device", device.id, request_id)
            return device_view(device)

    async def revoke_device(self, user_id, device_id, actor, request_id):
        async with transaction(self.engine) as db:
            await self._user(db, user_id)
            device = await db.scalar(
                select(Device)
                .where(Device.id == device_id, Device.user_id == user_id)
                .with_for_update()
            )
            if device is None:
                raise VpnError(404, "Device not found")
            if device.enabled:
                device.enabled = False
                device.revoked_at = await db.scalar(select(func.clock_timestamp()))
                self.audit(db, actor, "device.revoke", "device", device.id, request_id)
            for credential in (
                await db.scalars(
                    select(DeviceCredential)
                    .where(DeviceCredential.device_id == device_id)
                    .with_for_update()
                )
            ).all():
                await self._revoke_credential(db, credential, actor, request_id)
            return device_view(device)

    @staticmethod
    def _require_active(user):
        if not user.enabled or (user.expires_at and user.expires_at <= datetime.now(UTC)):
            raise VpnError(409, "VPN user unavailable")

    async def _accessible(self, db, user, server_id):
        server = await db.get(Server, server_id)
        if server is None or not server.enabled:
            raise VpnError(404, "Server not available")
        if user.access_mode == "selected" and not await db.get(ServerAccess, (user.id, server_id)):
            raise VpnError(403, "Server access denied")
        return server

    async def list_credentials(self, user_id, device_id):
        async with transaction(self.engine) as db:
            await self._device(db, user_id, device_id)
            return [
                credential_view(c)
                for c in (
                    await db.scalars(
                        select(DeviceCredential).where(DeviceCredential.device_id == device_id)
                    )
                ).all()
            ]

    async def _device(self, db, user_id, device_id):
        device = await db.scalar(
            select(Device).where(Device.id == device_id, Device.user_id == user_id)
        )
        if device is None:
            raise VpnError(404, "Device not found")
        return device

    async def _job(self, db, credential, operation, actor, request_id):
        existing = await db.scalar(
            select(Job).where(
                Job.type == operation,
                Job.target_type == "credential",
                Job.target_id == credential.id,
                Job.status.in_(("queued", "running")),
            )
        )
        if existing:
            return existing
        job = Job(
            type=operation,
            target_type="credential",
            target_id=credential.id,
            created_by=actor,
            request_id=request_id,
            idempotency_key=sha256(f"{operation}:{credential.id}:{uuid4()}".encode()).hexdigest(),
            replay_safe=True,
            cancellable=False,
        )
        db.add(job)
        await db.flush()
        record(job, "queued", await db.scalar(select(func.clock_timestamp())))
        return job

    async def create_credential(self, user_id, device_id, server_id, actor, request_id):
        if self.cipher is None:
            raise VpnError(503, "Management encryption unavailable")
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            self._require_active(user)
            device = await self._device(db, user_id, device_id)
            if not device.enabled:
                raise VpnError(409, "Device revoked")
            server = await self._accessible(db, user, server_id)
            if not server.ssh_host_key or not server.ssh_private_ciphertext:
                raise VpnError(409, "Server management unavailable")
            credential = await db.scalar(
                select(DeviceCredential)
                .where(
                    DeviceCredential.device_id == device_id, DeviceCredential.server_id == server_id
                )
                .options(undefer(DeviceCredential.secret_ciphertext))
                .with_for_update()
            )
            if credential is None:
                credential = DeviceCredential(
                    device_id=device_id,
                    server_id=server_id,
                    username="ttcp_" + uuid4().hex,
                    secret_ciphertext=self.cipher.encrypt(secrets.token_urlsafe(48).encode()),
                    status="pending",
                )
                db.add(credential)
                await db.flush()
            elif credential.status == "revoking":
                raise VpnError(409, "Credential revocation pending")
            elif credential.status == "revoked":
                credential.secret_ciphertext = self.cipher.encrypt(
                    secrets.token_urlsafe(48).encode()
                )
                credential.status = "pending"
                credential.revoked_at = credential.applied_at = credential.downloaded_at = None
            elif credential.status == "pending" and credential.secret_ciphertext is None:
                # Pre-lifecycle metadata rows have no usable secret or guaranteed
                # managed-node username; issue one before creating the job.
                credential.username = "ttcp_" + uuid4().hex
                credential.secret_ciphertext = self.cipher.encrypt(
                    secrets.token_urlsafe(48).encode()
                )
            elif credential.status == "active":
                completed = await db.scalar(
                    select(Job)
                    .where(
                        Job.type == "credential.create",
                        Job.target_id == credential.id,
                        Job.status == "succeeded",
                    )
                    .order_by(Job.created_at.desc())
                )
                if completed is None:
                    raise VpnError(409, "Credential already active")
                return completed
            job = await self._job(db, credential, "credential.create", actor, request_id)
            self.audit(
                db,
                actor,
                "credential.create",
                "credential",
                credential.id,
                request_id,
                {"job_id": str(job.id)},
            )
            return job

    async def _revoke_credential(self, db, credential, actor, request_id):
        if credential.status == "revoked":
            return None
        credential.status = "revoking"
        credential.revoked_at = await db.scalar(select(func.clock_timestamp()))
        credential.downloaded_at = credential.revoked_at  # Invalidate handoff immediately.
        job = await self._job(db, credential, "credential.revoke", actor, request_id)
        self.audit(
            db,
            actor,
            "credential.revoke",
            "credential",
            credential.id,
            request_id,
            {"job_id": str(job.id)},
        )
        return job

    async def revoke_credential(self, user_id, device_id, server_id, actor, request_id):
        async with transaction(self.engine) as db:
            await self._user(db, user_id)
            await self._device(db, user_id, device_id)
            credential = await db.scalar(
                select(DeviceCredential)
                .where(
                    DeviceCredential.device_id == device_id, DeviceCredential.server_id == server_id
                )
                .with_for_update()
            )
            if credential is None:
                raise VpnError(404, "Credential not found")
            job = await self._revoke_credential(db, credential, actor, request_id)
            return job, credential_view(credential)

    async def _revoke_user(self, db, user, actor, request_id):
        credentials = (
            await db.scalars(
                select(DeviceCredential)
                .join(Device, Device.id == DeviceCredential.device_id)
                .where(Device.user_id == user.id, DeviceCredential.status != "revoked")
                .with_for_update()
            )
        ).all()
        for credential in credentials:
            await self._revoke_credential(db, credential, actor, request_id)

    async def download(self, user_id, device_id, server_id, actor, request_id):
        if self.cipher is None:
            raise VpnError(503, "Management encryption unavailable")
        async with transaction(self.engine) as db:
            user = await self._user(db, user_id)
            self._require_active(user)
            device = await self._device(db, user_id, device_id)
            if not device.enabled:
                raise VpnError(409, "Device revoked")
            server = await self._accessible(db, user, server_id)
            credential = await db.scalar(
                select(DeviceCredential)
                .where(
                    DeviceCredential.device_id == device_id, DeviceCredential.server_id == server_id
                )
                .options(undefer(DeviceCredential.secret_ciphertext))
                .with_for_update()
            )
            if credential is None or credential.status != "active" or credential.downloaded_at:
                raise VpnError(409, "Download unavailable")
            try:
                password = self.cipher.decrypt(credential.secret_ciphertext).decode()
            except (InvalidToken, ValueError, UnicodeError, TypeError):
                raise VpnError(503, "Credential unavailable") from None
            credential.downloaded_at = await db.scalar(select(func.clock_timestamp()))
            self.audit(db, actor, "credential.download", "credential", credential.id, request_id)
            return {
                "username": credential.username,
                "password": password,
                "server": server.domain or str(server.public_ip or server.hostname),
            }

    async def expire_due(self):
        # Called repeatedly by the worker; row locks prevent duplicate transitions.
        async with transaction(self.engine) as db:
            users = (
                await db.scalars(
                    select(VpnUser)
                    .where(VpnUser.enabled, VpnUser.expires_at <= func.clock_timestamp())
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for user in users:
                user.enabled = False
                await self._revoke_user(db, user, None, "user.expire")
                self.audit(db, None, "user.expire", "vpn_user", user.id, "user.expire")
            return len(users)
