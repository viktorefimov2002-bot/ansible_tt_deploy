"""One-time invitations and separate client bearer sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0007_client_auth"
down_revision = "0006_vpn_lifecycle"
branch_labels = depends_on = None


def upgrade():
    for table in ("invitations", "client_sessions"):
        op.create_table(
            table,
            sa.Column(
                "id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("vpn_users.id"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True)),
        )
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])
        op.create_index(f"ix_{table}_expires_at", table, ["expires_at"])
    op.add_column("invitations", sa.Column("used_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_table("client_sessions")
    op.drop_table("invitations")
