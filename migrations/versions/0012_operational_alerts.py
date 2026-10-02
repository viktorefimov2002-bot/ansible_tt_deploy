"""Durable alert debounce, observation cursors and active incident identity."""

import sqlalchemy as sa
from alembic import op

revision = "0012_operational_alerts"
down_revision = "0011_config_revisions"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "operational_alerts",
        sa.Column("server_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("incident_id", sa.Uuid()),
        sa.Column("opened_at", sa.DateTime(timezone=True)),
        sa.Column("pending_state", sa.Boolean()),
        sa.Column("pending_since", sa.DateTime(timezone=True)),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "type IN ('NODE_OFFLINE','SERVICE_DOWN','DISK_HIGH','MEMORY_HIGH')",
            name=op.f("ck_operational_alerts_type"),
        ),
        sa.CheckConstraint(
            "(incident_id IS NULL) = (opened_at IS NULL)",
            name=op.f("ck_operational_alerts_incident"),
        ),
        sa.CheckConstraint(
            "(pending_state IS NULL) = (pending_since IS NULL)",
            name=op.f("ck_operational_alerts_pending"),
        ),
        sa.ForeignKeyConstraint(
            ["server_id"], ["servers.id"], name=op.f("fk_operational_alerts_server_id_servers")
        ),
        sa.PrimaryKeyConstraint("server_id", "type", name=op.f("pk_operational_alerts")),
    )


def downgrade():
    # Immutable audit transitions/notification receipts remain as history.
    op.drop_table("operational_alerts")
