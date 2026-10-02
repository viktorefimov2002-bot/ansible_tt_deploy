"""Durable lifecycle intent and independently tracked workload state."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010_server_lifecycle"
down_revision = "0009_notifications"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column("parameters", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "servers",
        sa.Column("lifecycle_state", sa.String(32), nullable=False, server_default="unknown"),
    )
    op.add_column("servers", sa.Column("lifecycle_job_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_servers_lifecycle_job_id_jobs", "servers", "jobs", ["lifecycle_job_id"], ["id"]
    )
    op.add_column(
        "servers", sa.Column("preflight_passed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_servers_lifecycle_state",
        "servers",
        "lifecycle_state IN ('unknown','installed','uninstalled','failed',"
        "'deploy_pending','update_pending','restart_pending','uninstall_pending')",
    )


def downgrade():
    op.drop_constraint("ck_servers_lifecycle_state", "servers", type_="check")
    op.drop_column("servers", "preflight_passed_at")
    op.drop_constraint("fk_servers_lifecycle_job_id_jobs", "servers", type_="foreignkey")
    op.drop_column("servers", "lifecycle_job_id")
    op.drop_column("servers", "lifecycle_state")
    op.drop_column("jobs", "parameters")
