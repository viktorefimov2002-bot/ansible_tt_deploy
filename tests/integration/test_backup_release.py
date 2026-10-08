"""Release drill against disposable PostgreSQL and a real HTTPS MinIO service.

No HTTP/object-store/dump/restore behavior is mocked. A local CA is supplied to
the transport because the test service certificate is intentionally disposable.
"""

import argparse
import hashlib
import io
import json
import os
import secrets
import shutil
import ssl
from pathlib import Path
from uuid import uuid4

import asyncpg
import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.auth_service import AuthService
from apps.api.client_auth_service import ClientAuthService
from apps.backups.__main__ import maintenance
from apps.backups.archive import EncryptArchive, validate
from apps.backups.config import BackupConfig, BackupFailure
from apps.backups.postgres import PostgresTools
from apps.backups.service import BackupService
from apps.backups.storage import S3Storage
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.persistence.database import database_url, transaction
from apps.persistence.models import Base, DeviceCredential, Server
from apps.shared.config import load_database_settings
from apps.vpn.schemas import DeviceInput, UserInput
from apps.vpn.service import VpnError, VpnService
from tests.test_auth import account
from tests.test_jobs import MemoryTransport
from tests.test_migrations import ROOT, migrate


def trusted_storage(config, ca_file):
    context = ssl.create_default_context(cafile=str(ca_file))
    return S3Storage(config, transport=httpx.AsyncHTTPTransport(verify=context))


def require_disposable_services():
    if os.environ.get("TTCP_TEST_BACKUP_STORAGE") != "1":
        if os.environ.get("TTCP_CI") == "1":
            pytest.fail("Mandatory backup storage drill requires scripts/ci/backup_validation.py")
        pytest.skip("Run scripts/ci/backup_validation.py against explicitly disposable services")
    settings = load_database_settings()
    assert settings.postgres_host in {"127.0.0.1", "localhost"}
    assert settings.postgres_db.startswith("ttcp_ci_")
    assert shutil.which("pg_dump") and shutil.which("pg_restore")
    directory = Path(os.environ["TTCP_CI_BACKUP_DIR"]).resolve()
    service = json.loads((directory / "service.json").read_text(encoding="utf-8"))
    endpoint = httpx.URL(service["endpoint"])
    assert endpoint.scheme == "https" and endpoint.host == "127.0.0.1"
    ca_file = Path(service.pop("ca_file")).resolve()
    assert ca_file.parent == directory / "certs"
    return settings, service, ca_file


async def grants(engine, filename, role, variable):
    sql = (ROOT / "infra/compose/postgres" / filename).read_text()
    # These deployment files contain line comments with semicolons.
    sql = "\n".join(line.partition("--")[0] for line in sql.splitlines())
    sql = sql.replace(f':"{variable}"', f'"{role}"')
    async with engine.begin() as db:
        for statement in sql.split(";"):
            if statement.strip():
                await db.execute(text(statement))


async def seed(engine, application_key):
    auth = AuthService(engine, application_key, 3600)
    actor, credentials, _ = await account(auth)
    login = await auth.login(
        credentials["username"], credentials["password"], credentials["totp_code"], "backup-ci"
    )
    assert login is not None
    vpn = VpnService(engine, application_key)
    user = await vpn.create_user(
        UserInput(display_name="restore-ci-proof", device_limit=7, access_mode="all"),
        actor,
        "backup-ci",
    )
    device = await vpn.create_device(
        user["id"], DeviceInput(name="restore-ci-device"), actor, "backup-ci"
    )
    configuration = {"toml": "# disposable restore proof", "deep_link": "tt://disposable-ci"}
    async with transaction(engine) as db:
        # No managed node will be contacted, and the hostname is deliberately unresolvable.
        server = Server(
            name="restore-ci-node", hostname="node.disposable.invalid", ssh_user="synthetic"
        )
        db.add(server)
        await db.flush()
        db.add(
            DeviceCredential(
                device_id=device["id"],
                server_id=server.id,
                username="disposable-device",
                status="active",
                secret_ciphertext=vpn.cipher.encrypt(secrets.token_bytes(32)),
                config_ciphertext=vpn.cipher.encrypt(json.dumps(configuration).encode()),
            )
        )
        server_id = server.id
    client = ClientAuthService(engine, 3600)
    used = await client.create_invitation(user["id"], 3600, actor, "backup-ci")
    exchanged = await client.exchange(used["token"], "backup-ci")
    assert exchanged is not None
    unused = await client.create_invitation(user["id"], 3600, actor, "backup-ci")
    return (
        user["id"],
        device["id"],
        server_id,
        login[0],
        exchanged[0],
        unused["token"],
        configuration,
    )


def protected(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


async def overwrite_disposable(storage, identity, ciphertext):
    """Test operator mutation of one known UUID; never a product storage capability."""
    url, headers = storage.signed(
        "PUT",
        identity,
        hashlib.sha256(ciphertext).hexdigest(),
        {"content-length": str(len(ciphertext))},
    )
    response = await storage.client.put(url, headers=headers, content=ciphertext)
    assert response.status_code == 200


async def test_real_encrypted_storage_restore_grants_and_access_quarantine(tmp_path, monkeypatch):
    settings, service, ca_file = require_disposable_services()
    source, target = "ttcp_ci_source_" + uuid4().hex, "ttcp_ci_restore_" + uuid4().hex
    reader, runtime = "ttcp_ci_reader_" + uuid4().hex, "ttcp_ci_runtime_" + uuid4().hex
    reader_password = secrets.token_hex(32)
    application_key = Fernet.generate_key().decode()
    owner = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        ssl=False,
    )
    engine = create_async_engine(database_url(settings).set(database=source), hide_parameters=True)
    restored = create_async_engine(
        database_url(settings).set(database=target), hide_parameters=True
    )
    config = BackupConfig(
        **service,
        region="us-east-1",
        bucket="ttcp-ci-backups",
        prefix="drill-" + uuid4().hex,
        active_key_id="ci-only",
        encryption_keys={"ci-only": secrets.token_hex(32)},
        postgres={
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "database": source,
            "user": reader,
            "password": reader_password,
            "sslmode": "disable",
        },
    )
    storage = trusted_storage(config, ca_file)
    try:
        await owner.execute(f'CREATE DATABASE "{source}"')
        await owner.execute(f'CREATE DATABASE "{target}"')
        # Generated identifiers/password contain hexadecimal characters only.
        await owner.execute(
            f"CREATE ROLE \"{reader}\" LOGIN NOINHERIT PASSWORD '{reader_password}'"
        )
        await owner.execute(f'CREATE ROLE "{runtime}" NOLOGIN NOINHERIT')
        await migrate(engine)
        await grants(engine, "backup-grants.sql", reader, "backup_role")
        state = await seed(engine, application_key)
        user_id, device_id, server_id, admin_token, client_token, unused_token, expected = state
        reader_engine = create_async_engine(
            database_url(settings).set(database=source, username=reader, password=reader_password),
            hide_parameters=True,
        )
        try:
            async with reader_engine.connect() as db:
                assert await db.scalar(text("SELECT count(*) FROM devices")) == 1
            with pytest.raises(DBAPIError) as denied:
                async with reader_engine.begin() as db:
                    await db.execute(
                        text("INSERT INTO vpn_users(display_name) VALUES ('forbidden')")
                    )
            assert denied.value.orig.sqlstate == "42501"
        finally:
            await reader_engine.dispose()

        transport = MemoryTransport()
        jobs = JobService(engine, transport, transport)
        backups = BackupService(jobs, enabled=True)
        job = await backups.request(None, "backup-ci", uuid4())
        worker = Worker(jobs, {"backup.run": backups.handler(config, storage, PostgresTools())})
        await worker.execute(job.id)
        assert (await jobs.get(job.id)).status == "succeeded"
        file = io.BytesIO()
        assert await storage.download(job.id, file)
        ciphertext = file.getvalue()
        assert b"restore-ci-proof" not in ciphertext
        assert b"disposable restore proof" not in ciphertext
        validate(file, config, job.id)
        # Real server enforces the conditional write; a second archive cannot replace it.
        competing = io.BytesIO()
        writer = EncryptArchive(competing, config, job.id)
        writer.update(b"PGDMPnot-the-original-database")
        writer.finish()
        await storage.put_if_absent(job.id, competing)
        downloaded = io.BytesIO()
        assert await storage.download(job.id, downloaded)
        assert downloaded.getvalue() == ciphertext
        await worker.execute(job.id)  # Duplicate terminal delivery cannot duplicate work/audit.
        async with engine.connect() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM audit_events WHERE action='backup.succeeded'")
                )
                == 1
            )
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
            source_audit = await db.scalar(text("SELECT count(*) FROM audit_events"))

        backup_file, target_file = tmp_path / "backup.json", tmp_path / "restore.json"
        payload = config.model_dump(mode="json")
        payload["access_key"] = config.access_key.get_secret_value()
        payload["secret_key"] = config.secret_key.get_secret_value()
        payload["encryption_keys"] = {
            name: value.get_secret_value() for name, value in config.encryption_keys.items()
        }
        payload["postgres"]["password"] = reader_password
        restore_target = payload["postgres"] | {
            "database": target,
            "user": settings.postgres_user,
            "password": settings.postgres_password.get_secret_value(),
        }
        protected(backup_file, payload)
        protected(target_file, restore_target)
        monkeypatch.setenv("TTCP_BACKUP_CONFIG_FILE", str(backup_file))
        monkeypatch.setenv("TTCP_RESTORE_CONFIG_FILE", str(target_file))
        # Trust the generated CA, retaining real TLS, DNS, sockets and server signing checks.
        monkeypatch.setattr(
            "apps.backups.__main__.S3Storage",
            lambda configured: trusted_storage(configured, ca_file),
        )
        args = argparse.Namespace(operation="verify", backup_id=job.id, confirm_database=None)
        await maintenance(args)
        args.operation, args.confirm_database = "restore", target
        wrong_key = payload | {"encryption_keys": {"ci-only": secrets.token_hex(32)}}
        protected(backup_file, wrong_key)
        with pytest.raises(BackupFailure):
            await maintenance(args)
        protected(backup_file, payload)
        for damaged in (
            ciphertext[:-1],
            ciphertext[:-16] + bytes([ciphertext[-16] ^ 1]) + ciphertext[-15:],
        ):
            await overwrite_disposable(storage, job.id, damaged)
            with pytest.raises(BackupFailure):
                await maintenance(args)
            async with restored.connect() as db:
                assert (
                    await db.scalar(
                        text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
                    )
                    == 0
                )
        await overwrite_disposable(storage, job.id, ciphertext)
        args.confirm_database = source
        with pytest.raises(BackupFailure):
            await maintenance(args)
        args.confirm_database = target
        await maintenance(args)
        async with restored.connect() as db:
            assert await db.scalar(text("SELECT version_num FROM alembic_version")) == revision
            assert (
                await db.scalar(
                    text("SELECT device_limit FROM vpn_users WHERE id=:id"), {"id": user_id}
                )
                == 7
            )
            assert await db.scalar(text("SELECT count(*) FROM devices")) == 1
            assert await db.scalar(text("SELECT count(*) FROM device_credentials")) == 1
            # Dump precedes its own final receipt/audit, so restore quarantines its running job.
            assert (
                await db.scalar(text("SELECT status FROM jobs WHERE id=:id"), {"id": job.id})
                == "running"
            )
            assert await db.scalar(text("SELECT count(*) FROM audit_events")) == source_audit - 1
            differences = await db.run_sync(
                lambda connection: compare_metadata(
                    MigrationContext.configure(connection, opts={"compare_server_default": True}),
                    Base.metadata,
                )
            )
            assert differences == []
        await migrate(restored)  # Restored head supports the deployment's idempotent migrator.
        with pytest.raises(BackupFailure):
            await maintenance(args)  # Never overwrite an occupied target.

        # The dump omits ACLs; recreated global roles need the real deployment grants.
        with pytest.raises(DBAPIError) as denied:
            async with restored.begin() as db:
                await db.execute(text(f'SET LOCAL ROLE "{runtime}"'))
                await db.execute(text("SELECT count(*) FROM devices"))
        assert denied.value.orig.sqlstate == "42501"
        await grants(restored, "runtime-grants.sql", runtime, "app_role")
        await grants(restored, "backup-grants.sql", reader, "backup_role")
        async with restored.begin() as db:
            await db.execute(text(f'SET LOCAL ROLE "{runtime}"'))
            assert await db.scalar(text("SELECT count(*) FROM devices")) == 1
            await db.execute(
                text("INSERT INTO vpn_users(display_name) VALUES ('recovered-runtime')")
            )
        for forbidden in ("DELETE FROM audit_events", "CREATE TABLE forbidden(id integer)"):
            with pytest.raises(DBAPIError) as denied:
                async with restored.begin() as db:
                    await db.execute(text(f'SET LOCAL ROLE "{runtime}"'))
                    await db.execute(text(forbidden))
            assert denied.value.orig.sqlstate == "42501"

        recovered_auth = AuthService(restored, application_key, 3600)
        recovered_client = ClientAuthService(restored, 3600)
        recovered_vpn = VpnService(restored, application_key)
        assert await recovered_auth.authenticate(admin_token) is not None
        principal = await recovered_client.authenticate(client_token)
        assert principal is not None
        delivered = await recovered_vpn.client_configuration(
            principal, device_id, server_id, "backup-ci-recovery"
        )
        assert (
            delivered == expected
        )  # Independently escrowed application key decrypts restored data.
        with pytest.raises(VpnError) as invalid_key:
            await VpnService(restored, Fernet.generate_key().decode()).client_configuration(
                principal, device_id, server_id, "backup-ci-wrong-key"
            )
        assert invalid_key.value.status == 503
        async with restored.begin() as db:
            for table in ("admin_sessions", "client_sessions", "invitations"):
                await db.execute(
                    text(
                        f"UPDATE {table} SET revoked_at=clock_timestamp() WHERE revoked_at IS NULL"
                    )
                )
            await db.execute(
                text("""
                UPDATE jobs SET status='failed', finished_at=clock_timestamp(),
                    claim_token=NULL, lease_until=NULL, error_code='unsafe_outcome',
                    error_message='Execution outcome requires reconciliation'
                WHERE status IN ('queued','running')
            """)
            )
            await db.execute(
                text("""
                INSERT INTO audit_events(actor_type,action,target_type,result)
                VALUES ('system','backup.restore','control_plane','success')
            """)
            )
        assert await recovered_auth.authenticate(admin_token) is None
        assert await recovered_client.authenticate(client_token) is None
        assert await recovered_client.exchange(unused_token, "backup-ci-recovery") is None
        with pytest.raises(VpnError) as invalid_session:
            await recovered_vpn.client_configuration(principal, device_id, server_id, "backup-ci")
        assert invalid_session.value.status == 401
        async with restored.connect() as db:
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM jobs WHERE status IN ('queued','running')")
                )
                == 0
            )
            assert (
                await db.scalar(
                    text("SELECT count(*) FROM audit_events WHERE action='backup.restore'")
                )
                == 1
            )
        # Wrong storage identity is rejected by the real service, not a fixture assertion.
        untrusted_storage = S3Storage(config)
        try:
            with pytest.raises(BackupFailure):
                await untrusted_storage.download(job.id, io.BytesIO())
        finally:
            await untrusted_storage.close()
        denied_storage = trusted_storage(
            config.model_copy(update={"secret_key": SecretStr(secrets.token_hex(32))}), ca_file
        )
        try:
            with pytest.raises(BackupFailure) as unauthorized:
                await denied_storage.download(job.id, io.BytesIO())
            assert unauthorized.value.code == "backup_failed"
        finally:
            await denied_storage.close()
    finally:
        await storage.close()
        await engine.dispose()
        await restored.dispose()
        for database in (source, target):
            await owner.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
        for role in (reader, runtime):
            await owner.execute(f'DROP ROLE IF EXISTS "{role}"')
        await owner.close()
