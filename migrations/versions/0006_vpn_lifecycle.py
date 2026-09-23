"""Track desired and observed per-device credential state.

Revision ID: 0006_vpn_lifecycle
Revises: 0005_servers
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_vpn_lifecycle"
down_revision = "0005_servers"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "device_credentials",
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
    )
    op.add_column("device_credentials", sa.Column("applied_at", sa.DateTime(timezone=True)))
    op.add_column("device_credentials", sa.Column("downloaded_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE device_credentials SET status = 'revoked' WHERE revoked_at IS NOT NULL")
    op.create_check_constraint(
        op.f("ck_device_credentials_status"),
        "device_credentials",
        "status IN ('pending','active','revoking','revoked')",
    )


def downgrade():
    op.drop_constraint(op.f("ck_device_credentials_status"), "device_credentials", type_="check")
    for column in ("downloaded_at", "applied_at", "status"):
        op.drop_column("device_credentials", column)
