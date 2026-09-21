"""Serialize device quotas and protect audit history from ordinary mutations."""

from alembic import op

revision = "0002_guards"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade():
    # A write to the parent serializes allocation, including at REPEATABLE READ
    # (where a stale allocator receives a serialization failure and must retry).
    op.execute("""
        CREATE FUNCTION enforce_device_quota() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE quota integer;
        BEGIN
            IF NEW.revoked_at IS NOT NULL THEN RETURN NEW; END IF;
            IF TG_OP = 'UPDATE' THEN
                IF OLD.user_id = NEW.user_id AND OLD.revoked_at IS NULL THEN
                    RETURN NEW;
                END IF;
            END IF;
            UPDATE vpn_users SET device_limit = device_limit
                WHERE id = NEW.user_id RETURNING device_limit INTO quota;
            IF quota IS NOT NULL AND
                (SELECT count(*) FROM devices
                 WHERE user_id = NEW.user_id AND revoked_at IS NULL AND id <> NEW.id) >= quota
            THEN
                RAISE EXCEPTION 'device quota exceeded'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_devices_user_quota';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER devices_quota BEFORE INSERT OR UPDATE OF user_id, revoked_at
        ON devices FOR EACH ROW EXECUTE FUNCTION enforce_device_quota()
    """)
    op.execute("""
        CREATE FUNCTION enforce_device_limit() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.device_limit < OLD.device_limit AND
                (SELECT count(*) FROM devices
                 WHERE user_id = NEW.id AND revoked_at IS NULL) > NEW.device_limit
            THEN
                RAISE EXCEPTION 'device limit is below allocated devices'
                    USING ERRCODE = '23514', CONSTRAINT = 'ck_vpn_users_allocated_devices';
            END IF;
            RETURN NEW;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER vpn_users_device_limit BEFORE UPDATE OF device_limit
        ON vpn_users FOR EACH ROW EXECUTE FUNCTION enforce_device_limit()
    """)
    op.execute("""
        CREATE FUNCTION protect_audit_events() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit events are immutable'
                USING ERRCODE = '23514', CONSTRAINT = 'ck_audit_events_immutable';
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE OR TRUNCATE
        ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION protect_audit_events()
    """)


def downgrade():
    op.execute("DROP TRIGGER audit_events_immutable ON audit_events")
    op.execute("DROP FUNCTION protect_audit_events()")
    op.execute("DROP TRIGGER vpn_users_device_limit ON vpn_users")
    op.execute("DROP FUNCTION enforce_device_limit()")
    op.execute("DROP TRIGGER devices_quota ON devices")
    op.execute("DROP FUNCTION enforce_device_quota()")
