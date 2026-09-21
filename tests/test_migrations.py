"""Offline checks plus real PostgreSQL tests in a randomly named disposable schema."""

import asyncio
import io
import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import selectinload

from apps.persistence.database import database_url, transaction
from apps.persistence.models import (
    Admin,
    AuditEvent,
    Base,
    Device,
    DeviceCredential,
    Server,
    ServerAccess,
    ServerConfigRevision,
    VpnUser,
)
from apps.shared.config import load_database_settings

ROOT = Path(__file__).resolve().parents[1]


def config():
    return Config(str(ROOT / "alembic.ini"))


def test_ordered_reproducible_offline_migrations():
    scripts = ScriptDirectory.from_config(config())
    assert scripts.get_heads() == ["0004_jobs"]
    assert [r.revision for r in scripts.walk_revisions()] == [
        "0004_jobs",
        "0003_admin_auth",
        "0002_guards",
        "0001_core",
    ]
    outputs = []
    for _ in range(2):
        cfg = config()
        cfg.output_buffer = io.StringIO()
        command.upgrade(cfg, "head", sql=True)
        outputs.append(cfg.output_buffer.getvalue())
    assert outputs[0] == outputs[1]
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table}" in outputs[0]
    cfg.output_buffer = io.StringIO()
    command.downgrade(cfg, "head:base", sql=True)
    assert "DROP TABLE devices" in cfg.output_buffer.getvalue()


async def migrate(engine, target="head", *, downgrade=False):
    def run(connection):
        cfg = config()
        cfg.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(cfg, target)

    async with engine.begin() as connection:
        await connection.run_sync(run)


@pytest.fixture
async def database():
    if os.environ.get("TTCP_TEST_POSTGRES") != "1":
        pytest.skip("Set TTCP_TEST_POSTGRES=1 and TTCP_POSTGRES_* for a disposable test database")
    settings = load_database_settings()
    owner = create_async_engine(database_url(settings), hide_parameters=True)
    schema = "ttcp_test_" + uuid4().hex
    async with owner.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        database_url(settings),
        hide_parameters=True,
        connect_args={"server_settings": {"search_path": schema, "statement_timeout": "10000"}},
    )
    try:
        await migrate(engine)
        yield engine
    finally:
        await engine.dispose()
        async with owner.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await owner.dispose()


async def seed(engine, *, limit=3, servers=2):
    async with transaction(engine) as session:
        user = VpnUser(display_name="Test user", device_limit=limit)
        nodes = [
            Server(name=f"node-{i}", hostname=f"node-{i}.test", ssh_user="manager")
            for i in range(servers)
        ]
        session.add_all([user, *nodes])
        await session.flush()
        return user.id, [node.id for node in nodes]


async def test_empty_upgrade_downgrade_reupgrade_and_model_drift(database):
    async with database.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert set(tables) == set(Base.metadata.tables) | {"alembic_version"}
        differences = await connection.run_sync(
            lambda conn: compare_metadata(
                MigrationContext.configure(conn, opts={"compare_server_default": True}),
                Base.metadata,
            )
        )
        assert differences == []
    await migrate(database)  # Head applied twice is a no-op.
    await migrate(database, "base", downgrade=True)
    async with database.connect() as connection:
        assert await connection.run_sync(lambda conn: inspect(conn).get_table_names()) == [
            "alembic_version"
        ]
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE pronamespace = current_schema()::regnamespace"
                )
            )
            == 0
        )
    await migrate(database)
    await seed(database)


async def test_defaults_and_orm_relationships(database):
    async with transaction(database) as session:
        user = VpnUser(display_name="Default quota")
        operator = Admin(username="observer")
        session.add_all([user, operator])
        await session.flush()
        assert user.device_limit == 3
        assert user.access_mode == "selected"
        assert operator.role == "viewer" and not operator.enabled
        assert user.created_at.tzinfo is not None
        device = Device(user=user, name="Laptop")
        server = Server(name="node", hostname="node.test", ssh_user="manager")
        session.add_all([device, server])
        await session.flush()
        session.add(ServerAccess(user=user, server=server))
        session.add(DeviceCredential(device=device, server=server, username="device-node"))
        user_id = user.id
    async with transaction(database) as session:
        user = await session.scalar(
            select(VpnUser)
            .where(VpnUser.id == user_id)
            .options(
                selectinload(VpnUser.devices).selectinload(Device.credentials),
                selectinload(VpnUser.server_access),
            )
        )
        assert len(user.devices) == len(user.server_access) == 1
        assert len(user.devices[0].credentials) == 1


@pytest.mark.parametrize("limit", [0, 1, 3, 5])
async def test_device_quota_is_not_credential_quota(database, limit):
    user, servers = await seed(database, limit=limit)
    async with transaction(database) as session:
        for i in range(limit):
            device = Device(user_id=user, name=f"device-{i}")
            session.add(device)
            await session.flush()
            for server in servers:
                session.add(
                    DeviceCredential(device_id=device.id, server_id=server, username=f"d{i}")
                )
    with pytest.raises(IntegrityError):
        async with transaction(database) as session:
            session.add(Device(user_id=user, name="over quota"))
    async with transaction(database) as session:
        assert await session.scalar(select(func.count()).select_from(Device)) == limit
        assert await session.scalar(select(func.count()).select_from(DeviceCredential)) == limit * 2


async def test_revoke_restore_transfer_and_limit_changes(database):
    user, _ = await seed(database, limit=1)
    async with transaction(database) as session:
        other = VpnUser(display_name="Other", device_limit=0)
        device = Device(user_id=user, name="one", enabled=False)
        session.add_all([other, device])
        await session.flush()
        device_id, other_id = device.id, other.id
    # Disabled devices still occupy slots; lowering below usage and transfer fail.
    for sql, parameters in [
        ("UPDATE vpn_users SET device_limit=0 WHERE id=:id", {"id": user}),
        ("UPDATE devices SET user_id=:other WHERE id=:id", {"id": device_id, "other": other_id}),
    ]:
        with pytest.raises(IntegrityError):
            async with database.begin() as connection:
                await connection.execute(text(sql), parameters)
    async with database.begin() as connection:
        await connection.execute(text("UPDATE devices SET revoked_at=now()"))
    async with transaction(database) as session:
        session.add(Device(user_id=user, name="replacement"))
    with pytest.raises(IntegrityError):
        async with database.begin() as connection:
            await connection.execute(
                text("UPDATE devices SET revoked_at=NULL WHERE id=:id"), {"id": device_id}
            )
    async with database.begin() as connection:
        await connection.execute(
            text("UPDATE vpn_users SET device_limit=2 WHERE id=:id"), {"id": user}
        )
        await connection.execute(
            text("UPDATE devices SET revoked_at=NULL WHERE id=:id"), {"id": device_id}
        )


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ"])
async def test_concurrent_allocation_cannot_exceed_quota(database, isolation):
    user, _ = await seed(database, limit=1)
    ready = asyncio.Event()
    started = 0

    async def allocate(name):
        nonlocal started
        try:
            async with database.connect() as connection:
                connection = await connection.execution_options(isolation_level=isolation)
                async with connection.begin():
                    await connection.execute(text("SELECT count(*) FROM devices"))
                    started += 1
                    if started == 2:
                        ready.set()
                    await asyncio.wait_for(ready.wait(), 5)
                    await connection.execute(
                        text("INSERT INTO devices(user_id,name) VALUES (:user,:name)"),
                        {"user": user, "name": name},
                    )
            return True
        except DBAPIError as exc:
            assert exc.orig.sqlstate in {"23514", "40001"}
            return False

    assert sorted(await asyncio.gather(allocate("a"), allocate("b"))) == [False, True]
    async with database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM devices")) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "INSERT INTO admins(username,role) VALUES ('bad','owner')",
        "INSERT INTO vpn_users(display_name,device_limit) VALUES ('bad',-1)",
        "INSERT INTO vpn_users(display_name,access_mode) VALUES ('bad','none')",
        "INSERT INTO servers(name,hostname,ssh_user,ssh_port) VALUES ('bad','h','u',0)",
        "INSERT INTO devices(user_id,name) VALUES (gen_random_uuid(),'orphan')",
        "INSERT INTO server_access(user_id,server_id) VALUES (gen_random_uuid(),gen_random_uuid())",
    ],
)
async def test_database_rejects_invalid_values_and_missing_parents(database, mutation):
    with pytest.raises(IntegrityError):
        async with database.begin() as connection:
            await connection.execute(text(mutation))


async def test_uniqueness_revision_ownership_and_restrict_deletes(database):
    user, servers = await seed(database)
    async with transaction(database) as session:
        device = Device(user_id=user, name="one")
        session.add(device)
        await session.flush()
        device_id = device.id
        session.add(DeviceCredential(device_id=device.id, server_id=servers[0], username="one"))
        session.add(ServerAccess(user_id=user, server_id=servers[0]))
        revision = ServerConfigRevision(server_id=servers[0], revision=1)
        session.add(revision)
        await session.flush()
        revision_id = revision.id
    for entity in [
        DeviceCredential(device_id=device_id, server_id=servers[0], username="duplicate"),
        ServerAccess(user_id=user, server_id=servers[0]),
        ServerConfigRevision(server_id=servers[0], revision=1),
        Server(name="node-0", hostname="another.test", ssh_user="manager"),
    ]:
        with pytest.raises(IntegrityError):
            async with transaction(database) as session:
                session.add(entity)
    async with database.begin() as connection:
        await connection.execute(
            text("UPDATE servers SET config_revision_id=:r WHERE id=:s"),
            {"r": revision_id, "s": servers[0]},
        )
    with pytest.raises(IntegrityError):
        async with database.begin() as connection:
            await connection.execute(
                text("UPDATE servers SET config_revision_id=:r WHERE id=:s"),
                {"r": revision_id, "s": servers[1]},
            )
    for table, identity in [("vpn_users", user), ("devices", device_id), ("servers", servers[0])]:
        with pytest.raises(IntegrityError):
            async with database.begin() as connection:
                await connection.execute(
                    text(f"DELETE FROM {table} WHERE id=:id"), {"id": identity}
                )


async def test_audit_is_immutable_and_survives_actor_deletion(database):
    async with transaction(database) as session:
        actor = Admin(username="temporary")
        session.add(actor)
        await session.flush()
        session.add(
            AuditEvent(
                actor_id=actor.id,
                actor_type="admin",
                action="user.create",
                target_type="vpn_user",
                target_id=uuid4(),
                result="success",
            )
        )
    async with database.begin() as connection:
        await connection.execute(text("DELETE FROM admins"))
    for sql in [
        "UPDATE audit_events SET result='changed'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ]:
        with pytest.raises(IntegrityError):
            async with database.begin() as connection:
                await connection.execute(text(sql))
    async with database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM audit_events")) == 1


async def test_transaction_rolls_back_errors_and_cancellation(database):
    for error in [RuntimeError, asyncio.CancelledError]:
        with pytest.raises(error):
            async with transaction(database) as session:
                session.add(VpnUser(display_name="rolled back"))
                await session.flush()
                raise error()
    async with database.connect() as connection:
        assert await connection.scalar(text("SELECT count(*) FROM vpn_users")) == 0


async def test_runtime_role_can_write_but_cannot_alter_schema_or_audit(database):
    role = "ttcp_runtime_test_" + uuid4().hex
    async with database.begin() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
        await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
    try:
        # Exercise the actual deployment grants against the disposable schema.
        grants = (ROOT / "infra/compose/postgres/runtime-grants.sql").read_text()
        grants = grants.replace(':"app_role"', f'"{role}"')
        grants = grants.replace("public.", f'"{schema}".').replace(
            "SCHEMA public", f'SCHEMA "{schema}"'
        )
        async with database.begin() as connection:
            for statement in grants.split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            operator = await connection.scalar(
                text("INSERT INTO admins(username) VALUES ('session-owner') RETURNING id")
            )
            await connection.execute(
                text(
                    "INSERT INTO admin_sessions(admin_id,token_hash,expires_at) "
                    "VALUES (:id,repeat('a',64),now()+interval '1 hour')"
                ),
                {"id": operator},
            )
            await connection.execute(text("UPDATE admin_sessions SET revoked_at=now()"))
            await connection.execute(text("DELETE FROM admin_sessions"))
            user = await connection.scalar(
                text("INSERT INTO vpn_users(display_name) VALUES ('runtime') RETURNING id")
            )
            await connection.execute(
                text("INSERT INTO devices(user_id,name) VALUES (:user,'runtime device')"),
                {"user": user},
            )
            await connection.execute(
                text(
                    "INSERT INTO audit_events(actor_type,action,target_type,result) "
                    "VALUES ('system','test','device','success')"
                )
            )
        for statement in [
            "CREATE TABLE forbidden(id integer)",
            "ALTER TABLE devices DISABLE TRIGGER devices_quota",
            "DELETE FROM audit_events",
            "UPDATE audit_events SET result='changed'",
            "TRUNCATE audit_events",
            "DELETE FROM alembic_version",
        ]:
            with pytest.raises(DBAPIError) as failure:
                async with database.begin() as connection:
                    await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                    await connection.execute(text(statement))
            assert failure.value.orig.sqlstate == "42501"
    finally:
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{role}"'))
            await connection.execute(text(f'DROP ROLE "{role}"'))
