"""Core durable identities. Secret values must be encrypted before persistence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "pk": "pk_%(table_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "ix": "ix_%(table_name)s_%(column_0_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True)}


class Identity:
    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Updated:
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Admin(Identity, Updated, Base):
    __tablename__ = "admins"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'viewer')", name="role"),
        CheckConstraint("length(trim(username)) > 0", name="username_nonblank"),
    )

    username: Mapped[str] = mapped_column(String(128), unique=True)
    # No default account or password; offline bootstrap explicitly enrolls MFA.
    password_hash: Mapped[str | None] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), server_default="viewer")
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    totp_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    totp_last_step: Mapped[int | None] = mapped_column(Integer)
    failed_logins: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    locked_until: Mapped[datetime | None]


class AdminSession(Identity, Base):
    __tablename__ = "admin_sessions"

    admin_id: Mapped[UUID] = mapped_column(ForeignKey("admins.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    revoked_at: Mapped[datetime | None]


class VpnUser(Identity, Updated, Base):
    __tablename__ = "vpn_users"
    __table_args__ = (
        CheckConstraint("device_limit >= 0", name="device_limit_nonnegative"),
        CheckConstraint("access_mode IN ('selected', 'all')", name="access_mode"),
        CheckConstraint("length(trim(display_name)) > 0", name="display_name_nonblank"),
    )

    display_name: Mapped[str] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    expires_at: Mapped[datetime | None]
    device_limit: Mapped[int] = mapped_column(Integer, server_default=text("3"))
    access_mode: Mapped[str] = mapped_column(String(16), server_default="selected")
    devices: Mapped[list["Device"]] = relationship(back_populates="user", passive_deletes="all")
    server_access: Mapped[list["ServerAccess"]] = relationship(
        back_populates="user", passive_deletes="all"
    )


class Server(Identity, Updated, Base):
    __tablename__ = "servers"
    __table_args__ = (
        CheckConstraint("ssh_port BETWEEN 1 AND 65535", name="ssh_port"),
        CheckConstraint("length(trim(name)) > 0", name="name_nonblank"),
        CheckConstraint("length(trim(hostname)) > 0", name="hostname_nonblank"),
        # The selected revision must belong to this server, not just exist.
        ForeignKeyConstraint(
            ["id", "config_revision_id"],
            ["server_config_revisions.server_id", "server_config_revisions.id"],
            name="fk_servers_current_revision",
            use_alter=True,
        ),
    )

    name: Mapped[str] = mapped_column(String(128), unique=True)
    hostname: Mapped[str] = mapped_column(String(255))
    public_ip: Mapped[str | None] = mapped_column(INET)
    domain: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), server_default="pending")
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    ssh_user: Mapped[str] = mapped_column(String(128))
    ssh_port: Mapped[int] = mapped_column(server_default=text("22"))
    ssh_host_key: Mapped[str | None] = mapped_column(String(8192))
    ssh_private_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    acme_http: Mapped[bool] = mapped_column(server_default=text("true"))
    trusttunnel_version: Mapped[str | None] = mapped_column(String(64))
    desired_trusttunnel_version: Mapped[str | None] = mapped_column(String(64))
    config_revision_id: Mapped[UUID | None]
    last_seen_at: Mapped[datetime | None]
    revisions: Mapped[list["ServerConfigRevision"]] = relationship(
        back_populates="server",
        foreign_keys="ServerConfigRevision.server_id",
        passive_deletes="all",
    )


class ServerAccess(Base):
    __tablename__ = "server_access"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("vpn_users.id"), primary_key=True)
    server_id: Mapped[UUID] = mapped_column(ForeignKey("servers.id"), primary_key=True)
    user: Mapped[VpnUser] = relationship(back_populates="server_access")
    server: Mapped[Server] = relationship()


class Device(Identity, Base):
    __tablename__ = "devices"
    __table_args__ = (CheckConstraint("length(trim(name)) > 0", name="name_nonblank"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("vpn_users.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    platform: Mapped[str | None] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    last_seen_at: Mapped[datetime | None]
    last_server_id: Mapped[UUID | None] = mapped_column(ForeignKey("servers.id"))
    revoked_at: Mapped[datetime | None]
    user: Mapped[VpnUser] = relationship(back_populates="devices")
    credentials: Mapped[list["DeviceCredential"]] = relationship(
        back_populates="device", passive_deletes="all"
    )


class DeviceCredential(Identity, Base):
    __tablename__ = "device_credentials"
    __table_args__ = (
        UniqueConstraint("device_id", "server_id"),
        UniqueConstraint("server_id", "username"),
        CheckConstraint("length(trim(username)) > 0", name="username_nonblank"),
    )

    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"))
    server_id: Mapped[UUID] = mapped_column(ForeignKey("servers.id"), index=True)
    username: Mapped[str] = mapped_column(String(128))
    # Nullable for metadata-only records; an issuing service must encrypt first.
    secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    revoked_at: Mapped[datetime | None]
    device: Mapped[Device] = relationship(back_populates="credentials")
    server: Mapped[Server] = relationship()


class ServerConfigRevision(Identity, Base):
    __tablename__ = "server_config_revisions"
    __table_args__ = (
        UniqueConstraint("server_id", "revision", name="uq_server_config_revisions_number"),
        UniqueConstraint("server_id", "id", name="uq_server_config_revisions_owner"),
        CheckConstraint("revision > 0", name="revision_positive"),
    )

    server_id: Mapped[UUID] = mapped_column(ForeignKey("servers.id"))
    revision: Mapped[int]
    # Only nonsensitive settings/references; never rendered client configurations.
    config_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("admins.id"))
    applied_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(32), server_default="pending")
    server: Mapped[Server] = relationship(back_populates="revisions", foreign_keys=[server_id])


class AuditEvent(Identity, Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("actor_type IN ('admin', 'vpn_user', 'system')", name="actor_type"),
        CheckConstraint("actor_type = 'system' OR actor_id IS NOT NULL", name="actor_required"),
    )

    # Historical polymorphic references intentionally survive target/actor deletion.
    actor_id: Mapped[UUID | None]
    actor_type: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(128))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(128), index=True)
    result: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))


class Job(Identity, Updated, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
        CheckConstraint(
            "attempts BETWEEN 0 AND max_attempts AND max_attempts BETWEEN 1 AND 5", name="attempts"
        ),
        CheckConstraint("jsonb_array_length(history) <= 200", name="history_bound"),
        CheckConstraint(
            "(status = 'running') = (claim_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="claim",
        ),
        CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="finished",
        ),
    )

    type: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID | None]
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("admins.id"))
    request_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(16), server_default="queued", index=True)
    progress: Mapped[int] = mapped_column(server_default=text("0"))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(server_default=text("3"))
    replay_safe: Mapped[bool] = mapped_column(server_default=text("false"))
    cancellable: Mapped[bool] = mapped_column(server_default=text("false"))
    available_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    dispatch_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    cancel_requested_at: Mapped[datetime | None]
    claim_token: Mapped[UUID | None]
    lease_until: Mapped[datetime | None] = mapped_column(index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(255))
    log_sequence: Mapped[int] = mapped_column(server_default=text("0"))
    history: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    result: Mapped[dict | None] = mapped_column(JSONB)
