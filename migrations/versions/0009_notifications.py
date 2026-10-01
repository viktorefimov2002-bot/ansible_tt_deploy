"""Durable per-operator read state for immutable operational event notifications."""

import sqlalchemy as sa
from alembic import op

revision = "0009_notifications"
down_revision = "0008_client_config"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "notification_reads",
        sa.Column("admin_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "read_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"], ["admins.id"], name=op.f("fk_notification_reads_admin_id_admins")
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["audit_events.id"],
            name=op.f("fk_notification_reads_event_id_audit_events"),
        ),
        sa.PrimaryKeyConstraint("admin_id", "event_id", name=op.f("pk_notification_reads")),
    )
    op.create_index("ix_audit_events_created_at_id", "audit_events", ["created_at", "id"])
    op.create_index(
        "ix_audit_events_action_result_created_at",
        "audit_events",
        ["action", "result", "created_at"],
    )


def downgrade():
    op.drop_index("ix_audit_events_action_result_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at_id", table_name="audit_events")
    op.drop_table("notification_reads")
