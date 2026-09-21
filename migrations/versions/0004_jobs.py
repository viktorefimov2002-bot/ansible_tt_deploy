"""Durable job intent, fenced attempts and bounded troubleshooting history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_jobs"
down_revision = "0003_admin_auth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.Uuid()),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("admins.id")),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("replay_safe", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cancellable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "dispatch_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_message", sa.String(255)),
        sa.Column("log_sequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "history", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','cancelled')", name="status"
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="progress"),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND max_attempts AND max_attempts BETWEEN 1 AND 5", name="attempts"
        ),
        sa.CheckConstraint("jsonb_array_length(history) <= 200", name="history_bound"),
        sa.CheckConstraint(
            "(status = 'running') = (claim_token IS NOT NULL AND lease_until IS NOT NULL)",
            name="claim",
        ),
        sa.CheckConstraint(
            "(status IN ('succeeded','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="finished",
        ),
    )
    for column in ("status", "available_at", "dispatch_at", "lease_until"):
        op.create_index(f"ix_jobs_{column}", "jobs", [column])


def downgrade():
    op.drop_table("jobs")
