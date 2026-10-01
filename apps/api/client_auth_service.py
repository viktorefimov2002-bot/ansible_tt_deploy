"""Separate VPN-user invitation and session authentication domain."""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select

from apps.api.auth_service import token_digest
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, ClientSession, Invitation, VpnUser


@dataclass(frozen=True)
class ClientPrincipal:
    user_id: UUID
    session_id: UUID
    expires_at: datetime


def audit(db, actor_type, actor_id, action, target_type, target_id, request_id, result="success"):
    db.add(
        AuditEvent(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            result=result,
        )
    )


def invitation_view(invitation):
    return {
        "id": invitation.id,
        "user_id": invitation.user_id,
        "created_at": invitation.created_at,
        "expires_at": invitation.expires_at,
        "used_at": invitation.used_at,
        "revoked_at": invitation.revoked_at,
    }


class ClientAuthService:
    def __init__(self, engine, session_seconds=28800):
        self.engine = engine
        self.session_seconds = session_seconds

    async def create_invitation(self, user_id, lifetime_seconds, actor_id, request_id):
        async with transaction(self.engine) as db:
            user = await db.scalar(select(VpnUser).where(VpnUser.id == user_id).with_for_update())
            now = await db.scalar(select(func.clock_timestamp()))
            if user is None:
                return None
            if not user.enabled or (user.expires_at and user.expires_at <= now):
                raise ValueError("VPN user unavailable")
            token = secrets.token_urlsafe(32)
            invitation = Invitation(
                user_id=user_id,
                token_hash=token_digest(token),
                expires_at=now + timedelta(seconds=lifetime_seconds),
            )
            db.add(invitation)
            await db.flush()
            audit(
                db,
                "admin",
                actor_id,
                "client.invitation.create",
                "invitation",
                invitation.id,
                request_id,
            )
            return invitation_view(invitation) | {"token": token}

    async def list_invitations(self, user_id):
        async with transaction(self.engine) as db:
            if await db.get(VpnUser, user_id) is None:
                return None
            rows = (
                await db.scalars(
                    select(Invitation)
                    .where(Invitation.user_id == user_id)
                    .order_by(Invitation.created_at.desc())
                )
            ).all()
            return [invitation_view(row) for row in rows]

    async def get_invitation(self, user_id, invitation_id):
        async with transaction(self.engine) as db:
            row = await db.scalar(
                select(Invitation).where(
                    Invitation.id == invitation_id, Invitation.user_id == user_id
                )
            )
            return invitation_view(row) if row else None

    async def revoke_invitation(self, user_id, invitation_id, actor_id, request_id):
        async with transaction(self.engine) as db:
            row = await db.scalar(
                select(Invitation)
                .where(Invitation.id == invitation_id, Invitation.user_id == user_id)
                .with_for_update()
            )
            if row is None:
                return False
            if row.revoked_at is None:
                row.revoked_at = await db.scalar(select(func.clock_timestamp()))
                audit(
                    db,
                    "admin",
                    actor_id,
                    "client.invitation.revoke",
                    "invitation",
                    row.id,
                    request_id,
                )
            return True

    async def exchange(self, token, request_id):
        async with transaction(self.engine) as db:
            # Lock the VPN user before the invitation, matching disable's lock
            # order. Re-read the invitation under lock after any competing exchange.
            digest = token_digest(token)
            user_id = await db.scalar(
                select(Invitation.user_id).where(Invitation.token_hash == digest)
            )
            user = (
                await db.scalar(select(VpnUser).where(VpnUser.id == user_id).with_for_update())
                if user_id
                else None
            )
            row = (
                await db.scalar(
                    select(Invitation).where(Invitation.token_hash == digest).with_for_update()
                )
                if user
                else None
            )
            now = await db.scalar(select(func.clock_timestamp()))
            if (
                row is None
                or row.used_at is not None
                or row.revoked_at is not None
                or row.expires_at <= now
                or user is None
                or not user.enabled
                or (user.expires_at and user.expires_at <= now)
            ):
                audit(
                    db,
                    "system",
                    None,
                    "client.invitation.exchange",
                    "invitation",
                    None,
                    request_id,
                    "denied",
                )
                return None
            row.used_at = now
            session_token = secrets.token_urlsafe(32)
            expires = now + timedelta(seconds=self.session_seconds)
            session = ClientSession(
                user_id=row.user_id, token_hash=token_digest(session_token), expires_at=expires
            )
            db.add(session)
            await db.flush()
            audit(
                db,
                "vpn_user",
                row.user_id,
                "client.invitation.exchange",
                "invitation",
                row.id,
                request_id,
            )
            return session_token, expires

    async def authenticate(self, token):
        async with transaction(self.engine) as db:
            row = (
                await db.execute(
                    select(ClientSession.id, ClientSession.user_id, ClientSession.expires_at)
                    .join(VpnUser, VpnUser.id == ClientSession.user_id)
                    .where(
                        ClientSession.token_hash == token_digest(token),
                        ClientSession.revoked_at.is_(None),
                        ClientSession.expires_at > func.clock_timestamp(),
                        VpnUser.enabled.is_(True),
                        (
                            VpnUser.expires_at.is_(None)
                            | (VpnUser.expires_at > func.clock_timestamp())
                        ),
                    )
                )
            ).first()
            return ClientPrincipal(row.user_id, row.id, row.expires_at) if row else None

    async def revoke_session(self, principal, request_id):
        async with transaction(self.engine) as db:
            row = await db.get(ClientSession, principal.session_id, with_for_update=True)
            if row and row.revoked_at is None:
                row.revoked_at = await db.scalar(select(func.clock_timestamp()))
                audit(
                    db,
                    "vpn_user",
                    principal.user_id,
                    "client.session.revoke",
                    "client_session",
                    row.id,
                    request_id,
                )
