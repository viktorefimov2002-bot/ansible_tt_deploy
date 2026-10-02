"""Disposable full-schema pg_dump -> encrypted S3 adapter -> maintenance restore."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from uuid import uuid4

import asyncpg
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from apps.backups.__main__ import maintenance
from apps.backups.config import BackupFailure
from apps.backups.postgres import PostgresTools
from apps.backups.service import BackupService
from apps.backups.storage import S3Storage
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.persistence.database import database_url
from apps.shared.config import load_database_settings
from tests.test_backups import ObjectStore, backup_config  # noqa: F401
from tests.test_jobs import MemoryTransport
from tests.test_migrations import migrate


async def test_disposable_backup_restore_cli_verification(backup_config, tmp_path, monkeypatch):  # noqa: F811
    if os.environ.get("TTCP_TEST_BACKUP_ROUNDTRIP") != "1":
        pytest.skip("Requires explicitly disposable PostgreSQL and PostgreSQL 17 tools on PATH")
    assert shutil.which("pg_dump") and shutil.which("pg_restore")
    settings = load_database_settings()
    source, target = "ttcp_source_" + uuid4().hex, "ttcp_restore_" + uuid4().hex
    owner = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        database=settings.postgres_db,
        ssl=False,
    )
    engine = create_async_engine(database_url(settings).set(database=source), hide_parameters=True)
    peer = ObjectStore()
    config = backup_config.model_copy(
        update={
            "postgres": backup_config.postgres.model_copy(
                update={
                    "host": settings.postgres_host,
                    "port": settings.postgres_port,
                    "database": source,
                    "user": settings.postgres_user,
                    "password": settings.postgres_password,
                }
            )
        }
    )
    storage = S3Storage(config, transport=httpx.MockTransport(peer.handle))
    try:
        await owner.execute(f'CREATE DATABASE "{source}"')
        await owner.execute(f'CREATE DATABASE "{target}"')
        await migrate(engine)
        async with engine.begin() as db:
            await db.execute(
                text("INSERT INTO vpn_users(display_name,device_limit) VALUES ('restore-proof',7)")
            )
            await db.execute(
                text("""
                INSERT INTO audit_events(actor_type,action,target_type,result)
                VALUES ('system','test.restore','vpn_user','success')
            """)
            )
        transport = MemoryTransport()
        jobs = JobService(engine, transport, transport)
        backups = BackupService(jobs, enabled=True)
        job = await backups.request(None, "restore-verification", uuid4())
        await Worker(
            jobs, {"backup.run": backups.handler(config, storage, PostgresTools())}
        ).execute(job.id)
        assert (await jobs.get(job.id)).status == "succeeded"
        assert len(peer.objects) == 1
        encrypted = next(iter(peer.objects.values()))
        assert b"restore-proof" not in encrypted

        def storage_factory(config):
            return S3Storage(config, transport=httpx.MockTransport(peer.handle))

        monkeypatch.setattr("apps.backups.__main__.S3Storage", storage_factory)
        protected = tmp_path / "backup.json"
        target_file = tmp_path / "restore.json"
        payload = config.model_dump(mode="json")
        payload.update(
            access_key="test-access",
            secret_key="test-secret",
            encryption_keys={"test-v1": "ab" * 32},
        )
        payload["postgres"]["password"] = settings.postgres_password.get_secret_value()
        protected.write_text(json.dumps(payload))
        protected.chmod(0o600)
        restore = payload["postgres"] | {"database": target}
        target_file.write_text(json.dumps(restore))
        target_file.chmod(0o600)
        monkeypatch.setenv("TTCP_BACKUP_CONFIG_FILE", str(protected))
        monkeypatch.setenv("TTCP_RESTORE_CONFIG_FILE", str(target_file))
        args = argparse.Namespace(operation="verify", backup_id=job.id, confirm_database=None)
        await maintenance(args)
        args.operation = "restore"
        with pytest.raises(BackupFailure):
            await maintenance(args)  # Explicit database confirmation is required.
        args.confirm_database = target
        empty = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=target,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            ssl=False,
        )
        try:
            await empty.execute("CREATE SCHEMA pgx_operator")
            with pytest.raises(BackupFailure):
                await maintenance(args)  # Non-system schemas also make a target occupied.
            await empty.execute("DROP SCHEMA pgx_operator")
        finally:
            await empty.close()
        await maintenance(args)
        restored = await asyncpg.connect(
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=target,
            user=settings.postgres_user,
            password=settings.postgres_password.get_secret_value(),
            ssl=False,
        )
        try:
            assert (
                await restored.fetchval(
                    "SELECT device_limit FROM vpn_users WHERE display_name='restore-proof'"
                )
                == 7
            )
            assert (
                await restored.fetchval(
                    "SELECT count(*) FROM audit_events WHERE action='test.restore'"
                )
                == 1
            )
            assert (
                await restored.fetchval("SELECT version_num FROM alembic_version")
                == "0012_operational_alerts"
            )
            assert (
                await restored.fetchval("SELECT type FROM jobs WHERE id=$1", job.id) == "backup.run"
            )
            # The dump snapshot precedes remote-success audit, which is documented.
            assert (
                await restored.fetchval(
                    "SELECT count(*) FROM audit_events WHERE action='backup.succeeded'"
                )
                == 0
            )
        finally:
            await restored.close()
        with pytest.raises(BackupFailure):
            await maintenance(args)  # Occupied target is refused.
        target_file.write_text(json.dumps(payload["postgres"]))
        args.confirm_database = source
        with pytest.raises(BackupFailure):
            await maintenance(args)  # Source database is refused.
        path = next(iter(peer.objects))
        peer.objects[path] = encrypted[:-1]
        args.operation = "verify"
        with pytest.raises(BackupFailure):
            await maintenance(args)
    finally:
        await storage.close()
        await engine.dispose()
        for name in (source, target):
            await owner.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await owner.close()


def test_maintenance_entrypoint_does_not_echo_secret_errors(tmp_path):
    path = tmp_path / "invalid.json"
    secret = "test-secret-do-not-output"
    path.write_text('{"endpoint": "' + secret + '"}')
    path.chmod(0o600)
    env = os.environ | {"TTCP_BACKUP_CONFIG_FILE": str(path)}
    result = subprocess.run(
        [sys.executable, "-m", "apps.backups", "verify", str(uuid4())],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1 and "Backup maintenance failed" in result.stdout
    assert secret not in result.stdout + result.stderr
