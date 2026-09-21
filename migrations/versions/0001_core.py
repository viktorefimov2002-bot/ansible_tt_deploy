"""Initial Control Plane identities and persistence relationships.

Revision ID: 0001_core
Revises: None
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admins",
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=True),
        sa.Column("role", sa.String(length=16), server_default="viewer", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('admin', 'viewer')", name=op.f("ck_admins_role")),
        sa.CheckConstraint("length(trim(username)) > 0", name=op.f("ck_admins_username_nonblank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admins")),
        sa.UniqueConstraint("username", name=op.f("uq_admins_username")),
    )
    op.create_table(
        "audit_events",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_type = 'system' OR actor_id IS NOT NULL",
            name=op.f("ck_audit_events_actor_required"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('admin', 'vpn_user', 'system')", name=op.f("ck_audit_events_actor_type")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        op.f("ix_audit_events_request_id"), "audit_events", ["request_id"], unique=False
    )
    op.create_table(
        "servers",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("public_ip", postgresql.INET(), nullable=True),
        sa.Column("domain", sa.String(length=255), nullable=True),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ssh_user", sa.String(length=128), nullable=False),
        sa.Column("ssh_port", sa.Integer(), server_default=sa.text("22"), nullable=False),
        sa.Column("trusttunnel_version", sa.String(length=64), nullable=True),
        sa.Column("desired_trusttunnel_version", sa.String(length=64), nullable=True),
        sa.Column("config_revision_id", sa.Uuid(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(hostname)) > 0", name=op.f("ck_servers_hostname_nonblank")),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_servers_name_nonblank")),
        sa.CheckConstraint("ssh_port BETWEEN 1 AND 65535", name=op.f("ck_servers_ssh_port")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_servers")),
        sa.UniqueConstraint("name", name=op.f("uq_servers_name")),
    )
    op.create_table(
        "vpn_users",
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_limit", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("access_mode", sa.String(length=16), server_default="selected", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "access_mode IN ('selected', 'all')", name=op.f("ck_vpn_users_access_mode")
        ),
        sa.CheckConstraint("device_limit >= 0", name=op.f("ck_vpn_users_device_limit_nonnegative")),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0", name=op.f("ck_vpn_users_display_name_nonblank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vpn_users")),
    )
    op.create_table(
        "devices",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_server_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(name)) > 0", name=op.f("ck_devices_name_nonblank")),
        sa.ForeignKeyConstraint(
            ["last_server_id"], ["servers.id"], name=op.f("fk_devices_last_server_id_servers")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["vpn_users.id"], name=op.f("fk_devices_user_id_vpn_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
    )
    op.create_index(op.f("ix_devices_user_id"), "devices", ["user_id"], unique=False)
    op.create_table(
        "server_access",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_server_access_server_id_servers")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["vpn_users.id"], name=op.f("fk_server_access_user_id_vpn_users")
        ),
        sa.PrimaryKeyConstraint("user_id", "server_id", name=op.f("pk_server_access")),
    )
    op.create_table(
        "server_config_revisions",
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0", name=op.f("ck_server_config_revisions_revision_positive")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["admins.id"], name=op.f("fk_server_config_revisions_created_by_admins")
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_server_config_revisions_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_config_revisions")),
        sa.UniqueConstraint("server_id", "id", name="uq_server_config_revisions_owner"),
        sa.UniqueConstraint("server_id", "revision", name="uq_server_config_revisions_number"),
    )
    op.create_table(
        "device_credentials",
        sa.Column("device_id", sa.Uuid(), nullable=False),
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(username)) > 0", name=op.f("ck_device_credentials_username_nonblank")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], name=op.f("fk_device_credentials_device_id_devices")
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_device_credentials_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_device_credentials")),
        sa.UniqueConstraint("device_id", "server_id", name=op.f("uq_device_credentials_device_id")),
        sa.UniqueConstraint("server_id", "username", name=op.f("uq_device_credentials_server_id")),
    )
    op.create_index(
        op.f("ix_device_credentials_server_id"), "device_credentials", ["server_id"], unique=False
    )
    op.create_foreign_key(
        "fk_servers_current_revision",
        "servers",
        "server_config_revisions",
        ["id", "config_revision_id"],
        ["server_id", "id"],
        use_alter=True,
    )


def downgrade():
    op.drop_constraint("fk_servers_current_revision", "servers", type_="foreignkey")
    op.drop_index(op.f("ix_device_credentials_server_id"), table_name="device_credentials")
    op.drop_table("device_credentials")
    op.drop_table("server_config_revisions")
    op.drop_table("server_access")
    op.drop_index(op.f("ix_devices_user_id"), table_name="devices")
    op.drop_table("devices")
    op.drop_table("vpn_users")
    op.drop_table("servers")
    op.drop_index(op.f("ix_audit_events_request_id"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("admins")
