"""Immutable configuration content, durable apply state and current revision tracking."""

import sqlalchemy as sa
from alembic import op

revision = "0011_config_revisions"
down_revision = "0010_server_lifecycle"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "servers", sa.Column("config_state", sa.String(32), nullable=False, server_default="idle")
    )
    op.add_column("servers", sa.Column("config_job_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_servers_config_job_id_jobs", "servers", "jobs", ["config_job_id"], ["id"]
    )
    op.create_check_constraint(
        "ck_servers_config_state",
        "servers",
        "config_state IN ('idle','apply_pending','applied','rolled_back',"
        "'rollback_failed','failed','unknown')",
    )
    op.add_column("server_config_revisions", sa.Column("idempotency_key", sa.String(128)))
    op.create_unique_constraint(
        "uq_server_config_revisions_idempotency_key", "server_config_revisions", ["idempotency_key"]
    )
    op.add_column("server_config_revisions", sa.Column("job_id", sa.UUID()))
    op.create_foreign_key(
        "fk_server_config_revisions_job_id_jobs",
        "server_config_revisions",
        "jobs",
        ["job_id"],
        ["id"],
    )
    op.add_column("server_config_revisions", sa.Column("failure_code", sa.String(64)))
    op.add_column("server_config_revisions", sa.Column("failure_message", sa.String(255)))
    op.add_column("server_config_revisions", sa.Column("previous_config_state", sa.String(32)))
    op.create_check_constraint(
        "ck_server_config_revisions_status",
        "server_config_revisions",
        "status IN ('pending','validated','apply_pending','applied','rolled_back',"
        "'rollback_failed','failed','unknown')",
    )
    op.execute("""
        CREATE FUNCTION protect_config_revision() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE','TRUNCATE') THEN
                RAISE EXCEPTION 'Configuration revision history is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF ROW(NEW.id, NEW.server_id, NEW.revision, NEW.config_json, NEW.created_by,
                   NEW.created_at, NEW.idempotency_key)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.server_id, OLD.revision, OLD.config_json, OLD.created_by,
                   OLD.created_at, OLD.idempotency_key) THEN
                RAISE EXCEPTION 'Configuration revision content is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.job_id IS NOT NULL AND
               (NEW.job_id IS DISTINCT FROM OLD.job_id OR
                NEW.previous_config_state IS DISTINCT FROM OLD.previous_config_state) THEN
                RAISE EXCEPTION 'Configuration revision apply identity is immutable'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER config_revision_immutable
            BEFORE UPDATE OR DELETE ON server_config_revisions
            FOR EACH ROW EXECUTE FUNCTION protect_config_revision()
    """)
    op.execute("""
        CREATE TRIGGER config_revision_no_truncate
            BEFORE TRUNCATE ON server_config_revisions
            FOR EACH STATEMENT EXECUTE FUNCTION protect_config_revision()
    """)


def downgrade():
    op.execute("DROP TRIGGER config_revision_no_truncate ON server_config_revisions")
    op.execute("DROP TRIGGER config_revision_immutable ON server_config_revisions")
    op.execute("DROP FUNCTION protect_config_revision()")
    op.drop_constraint(
        "ck_server_config_revisions_status", "server_config_revisions", type_="check"
    )
    op.drop_column("server_config_revisions", "failure_message")
    op.drop_column("server_config_revisions", "previous_config_state")
    op.drop_column("server_config_revisions", "failure_code")
    op.drop_constraint(
        "fk_server_config_revisions_job_id_jobs", "server_config_revisions", type_="foreignkey"
    )
    op.drop_column("server_config_revisions", "job_id")
    op.drop_constraint(
        "uq_server_config_revisions_idempotency_key", "server_config_revisions", type_="unique"
    )
    op.drop_column("server_config_revisions", "idempotency_key")
    op.drop_constraint("ck_servers_config_state", "servers", type_="check")
    op.drop_constraint("fk_servers_config_job_id_jobs", "servers", type_="foreignkey")
    op.drop_column("servers", "config_job_id")
    op.drop_column("servers", "config_state")
