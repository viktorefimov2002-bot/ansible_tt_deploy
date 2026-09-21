"""PostgreSQL-owned authentication state. No credentials in logs or audit metadata."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import pyotp
from anyio import CapacityLimiter, to_thread
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select, text
from sqlalchemy.orm import undefer

from apps.api.passwords import hash_password, verify_password
from apps.persistence.database import transaction
from apps.persistence.models import Admin, AdminSession, AuditEvent


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def audit(session, action, result, request_id, actor_id=None, target_id=None):
    session.add(
        AuditEvent(
            actor_type="admin" if actor_id else "system",
            actor_id=actor_id,
            action=action,
            target_type="admin_session" if "session" in action else "admin",
            target_id=target_id,
            result=result,
            request_id=request_id,
        )
    )


def matching_step(seed: str, code: str, now: datetime, last_step: int | None) -> int | None:
    step = int(now.timestamp()) // 30
    totp = pyotp.TOTP(seed)
    for candidate in (step, step - 1, step + 1):
        if candidate > (last_step if last_step is not None else -1):
            if secrets.compare_digest(totp.at(candidate * 30), code):
                return candidate
    return None


@dataclass(frozen=True)
class Principal:
    admin_id: UUID
    username: str
    role: str
    session_id: UUID


class AuthService:
    def __init__(self, engine, key: str, session_seconds: int):
        self.engine = engine
        self.cipher = Fernet(key.encode("ascii"))
        self.session_seconds = session_seconds
        # Limit expensive hash work even when the API is called without NGINX.
        self.hash_limiter = CapacityLimiter(2)

    async def login(self, username: str, password: str, code: str, request_id: str):
        async with transaction(self.engine) as db:
            account = await db.scalar(
                select(Admin)
                .options(undefer(Admin.totp_ciphertext))
                .where(Admin.username == username)
                .with_for_update()
            )
            now = await db.scalar(select(func.clock_timestamp()))
            locked = account and account.locked_until and account.locked_until > now
            valid_password = await to_thread.run_sync(
                verify_password,
                password,
                account.password_hash if account else None,
                limiter=self.hash_limiter,
            )
            step = None
            if account and account.enabled and not locked and valid_password:
                try:
                    seed = self.cipher.decrypt(account.totp_ciphertext or b"").decode("ascii")
                    step = matching_step(seed, code, now, account.totp_last_step)
                except (InvalidToken, ValueError, UnicodeError):
                    pass
            if step is None:
                if account and not locked:
                    account.failed_logins += 1
                    if account.failed_logins >= 5:
                        account.locked_until = now + timedelta(minutes=5)
                        account.failed_logins = 0
                audit(db, "auth.login", "denied", request_id, account.id if account else None)
                return None
            account.failed_logins = 0
            account.locked_until = None
            account.totp_last_step = step
            token = secrets.token_urlsafe(32)
            expires = now + timedelta(seconds=self.session_seconds)
            record = AdminSession(
                admin_id=account.id, token_hash=token_digest(token), expires_at=expires
            )
            db.add(record)
            await db.flush()
            audit(db, "auth.login", "success", request_id, account.id, account.id)
            return token, expires

    async def authenticate(self, token: str) -> Principal | None:
        async with transaction(self.engine) as db:
            row = (
                await db.execute(
                    select(Admin, AdminSession.id)
                    .join(AdminSession, AdminSession.admin_id == Admin.id)
                    .where(
                        AdminSession.token_hash == token_digest(token),
                        AdminSession.revoked_at.is_(None),
                        AdminSession.expires_at > func.clock_timestamp(),
                        Admin.enabled.is_(True),
                        Admin.role.in_(("admin", "viewer")),
                    )
                )
            ).first()
            if row:
                account, session_id = row
                return Principal(account.id, account.username, account.role, session_id)
            return None

    async def denied(self, principal: Principal | None, request_id: str):
        async with transaction(self.engine) as db:
            audit(
                db,
                "auth.authorization",
                "denied",
                request_id,
                principal.admin_id if principal else None,
            )

    async def revoke(self, principal: Principal, session_id: UUID, request_id: str) -> bool:
        async with transaction(self.engine) as db:
            record = await db.get(AdminSession, session_id, with_for_update=True)
            if record is None:
                return False
            record.revoked_at = record.revoked_at or await db.scalar(select(func.clock_timestamp()))
            audit(db, "auth.session_revoke", "success", request_id, principal.admin_id, record.id)
            return True

    async def bootstrap(self, username: str, password: str, seed: str, code: str):
        if not username.strip() or len(username) > 128:
            raise ValueError("Username must contain 1 to 128 nonblank characters")
        if len(seed) != 32 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for c in seed):
            raise ValueError("TOTP seed must be 32 uppercase base32 characters")
        encoded = await to_thread.run_sync(hash_password, password, limiter=self.hash_limiter)
        async with transaction(self.engine) as db:
            # Serialize first-admin creation across independent bootstrap processes.
            await db.execute(text("LOCK TABLE admins IN SHARE ROW EXCLUSIVE MODE"))
            if await db.scalar(select(Admin.id).where(Admin.role == "admin").limit(1)):
                raise ValueError("An administrator already exists; bootstrap refused")
            now = await db.scalar(select(func.clock_timestamp()))
            step = matching_step(seed, code, now, None)
            if step is None:
                raise ValueError("Invalid enrollment code")
            account = Admin(
                username=username,
                password_hash=encoded,
                role="admin",
                enabled=True,
                totp_ciphertext=self.cipher.encrypt(seed.encode("ascii")),
                totp_last_step=step,
            )
            db.add(account)
            await db.flush()
            audit(db, "auth.bootstrap", "success", None, account.id, account.id)
