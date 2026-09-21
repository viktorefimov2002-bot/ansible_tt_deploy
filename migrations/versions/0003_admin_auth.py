"""Administrative MFA and revocable sessions; existing identities remain unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "0003_admin_auth"
down_revision = "0002_guards"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("admins", sa.Column("totp_ciphertext", sa.LargeBinary(), nullable=True))
    op.add_column("admins", sa.Column("totp_last_step", sa.Integer(), nullable=True))
    op.add_column(
        "admins",
        sa.Column("failed_logins", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("admins", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("admin_id", sa.Uuid(), sa.ForeignKey("admins.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_admin_sessions_admin_id", "admin_sessions", ["admin_id"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])


def downgrade():
    op.drop_table("admin_sessions")
    for name in ("locked_until", "failed_logins", "totp_last_step", "totp_ciphertext"):
        op.drop_column("admins", name)
