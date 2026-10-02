"""Operator maintenance CLI. Never invoked through the API or job transport."""

import argparse
import asyncio
import os
import tempfile
from uuid import UUID

from apps.backups.archive import verify_archive
from apps.backups.config import BackupFailure, PostgresConfig, protected_config
from apps.backups.postgres import PostgresTools
from apps.backups.storage import S3Storage
from apps.shared.logging import configure_logging


async def maintenance(args):
    config = protected_config(os.environ.get("TTCP_BACKUP_CONFIG_FILE", ""))
    target = None
    if args.operation == "restore":
        target = protected_config(os.environ.get("TTCP_RESTORE_CONFIG_FILE", ""), PostgresConfig)
        if args.confirm_database != target.database:
            raise BackupFailure()
        if (target.host, target.port, target.database) == (
            config.postgres.host,
            config.postgres.port,
            config.postgres.database,
        ):
            raise BackupFailure()  # Restore into a new empty database; never overwrite the source.
    storage = S3Storage(config)
    try:
        async with asyncio.timeout(config.timeout_seconds):
            with tempfile.TemporaryFile(prefix="ttcp-encrypted-") as file:
                if not await storage.download(args.backup_id, file):
                    raise BackupFailure()
                await verify_archive(file, config, args.backup_id)
                if target is not None:
                    await PostgresTools().restore(file, config, args.backup_id, target)
    finally:
        await storage.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify or restore an encrypted remote TTCP backup"
    )
    parser.add_argument("operation", choices=("verify", "restore"))
    parser.add_argument("backup_id", type=UUID)
    parser.add_argument("--confirm-database")
    args = parser.parse_args(argv)
    configure_logging("backup-maintenance", "WARNING")
    try:
        asyncio.run(maintenance(args))
    except (Exception, KeyboardInterrupt):
        print("Backup maintenance failed; check protected configuration, remote object and target.")
        return 1
    print("Remote archive verified." if args.operation == "verify" else "Restore completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
