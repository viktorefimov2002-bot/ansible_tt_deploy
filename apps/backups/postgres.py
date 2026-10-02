"""Fixed PostgreSQL tools; no shell, DSNs, plaintext files or subprocess output logs."""

import asyncio

import asyncpg

from apps.backups.archive import CHUNK, EncryptArchive, plaintext, verify_archive
from apps.backups.config import BackupFailure


class PostgresTools:
    async def spawn(self, executable, args, config, *, stdin=None):
        try:
            return await asyncio.create_subprocess_exec(
                executable,
                *args,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=config.environment(),
            )
        except OSError:
            raise BackupFailure("backup_unavailable") from None

    async def cleanup(self, process):
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        # Drain bounded chunks after killing: a paused PIPE transport can otherwise
        # keep wait() blocked on cancellation or an archive size-limit failure.
        while await process.stdout.read(CHUNK):
            pass
        await process.wait()

    async def dump(self, file, config, backup_id):
        process = await self.spawn(
            "pg_dump",
            ["--format=custom", "--compress=0", "--no-owner", "--no-acl", "--no-password"],
            config.postgres,
        )
        try:
            writer = EncryptArchive(file, config, backup_id)
            while data := await process.stdout.read(CHUNK):
                writer.update(data)
            if await process.wait() != 0:
                raise BackupFailure()
            writer.finish()
            await verify_archive(file, config, backup_id)
        finally:
            await self.cleanup(process)

    async def restore(self, file, config, backup_id, target):
        await verify_archive(file, config, backup_id)  # Authenticate BEFORE connecting to target.
        await self.empty_target(target)
        process = await self.spawn(
            "pg_restore",
            [
                "--dbname=" + target.database,
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--exit-on-error",
                "--single-transaction",
            ],
            target,
            stdin=asyncio.subprocess.PIPE,
        )
        try:
            for data in plaintext(file, config, backup_id):
                process.stdin.write(data)
                await process.stdin.drain()
            process.stdin.close()
            if await process.wait() != 0:
                raise BackupFailure()
        except (BrokenPipeError, ConnectionResetError):
            raise BackupFailure() from None
        finally:
            await self.cleanup(process)

    async def empty_target(self, target):
        connection = None
        try:
            connection = await asyncpg.connect(
                host=target.host,
                port=target.port,
                database=target.database,
                user=target.user,
                password=target.password.get_secret_value(),
                ssl=False if target.sslmode == "disable" else target.sslmode,
                timeout=10,
                command_timeout=10,
            )
            occupied = await connection.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                    UNION ALL
                    SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                    UNION ALL
                    SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace
                    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
                    UNION ALL
                    SELECT 1 FROM pg_namespace
                    WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema')
                    UNION ALL
                    SELECT 1 FROM pg_largeobject_metadata
                )
            """)
            if occupied:
                raise BackupFailure()
        except Exception:
            raise BackupFailure() from None
        finally:
            if connection:
                await connection.close(timeout=5)
