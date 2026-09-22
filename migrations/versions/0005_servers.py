"""Encrypted management identity and structured diagnostic results."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_servers"
down_revision = "0004_jobs"
branch_labels = depends_on = None


def upgrade():
    op.add_column("servers", sa.Column("ssh_host_key", sa.String(8192)))
    op.add_column("servers", sa.Column("ssh_private_ciphertext", sa.LargeBinary()))
    op.add_column(
        "servers",
        sa.Column("acme_http", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column("jobs", sa.Column("result", JSONB()))


def downgrade():
    op.drop_column("jobs", "result")
    for column in ("acme_http", "ssh_private_ciphertext", "ssh_host_key"):
        op.drop_column("servers", column)
