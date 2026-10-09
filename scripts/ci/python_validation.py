"""Run Linux integration gates against newly created, loopback-only containers."""

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.networking import (  # noqa: E402
    PublishedNetwork,
    available_loopback_port,
    published_port,
    wait_tcp,
)


async def verify_dependencies(env):
    import asyncpg
    from redis.asyncio import Redis

    connection = None
    try:
        connection = await asyncpg.connect(
            host="127.0.0.1",
            port=int(env["TTCP_POSTGRES_PORT"]),
            database=env["TTCP_POSTGRES_DB"],
            user=env["TTCP_POSTGRES_USER"],
            password=env["TTCP_POSTGRES_PASSWORD"],
            timeout=5,
            command_timeout=5,
            ssl=False,
        )
        if await connection.fetchval("SELECT 1") != 1:
            raise RuntimeError
    except Exception:
        raise RuntimeError("Published PostgreSQL failed authenticated readiness") from None
    finally:
        if connection:
            await connection.close(timeout=5)
    redis = Redis(
        host="127.0.0.1",
        port=int(env["TTCP_REDIS_PORT"]),
        password=env["TTCP_REDIS_PASSWORD"],
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        if not await redis.ping():
            raise RuntimeError
    except Exception:
        raise RuntimeError("Published Redis failed authenticated readiness") from None
    finally:
        await redis.aclose()


def main():
    if sys.platform != "linux":
        raise SystemExit("This release gate requires Linux and a local Docker Engine")
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("TTCP_", "POSTGRES_", "PG", "REDIS_", "DOCKER_"))
    }
    env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    token = secrets.token_hex(8)
    network = "ttcp-ci-python-" + token
    postgres = "ttcp-ci-postgres-" + token
    redis = "ttcp-ci-redis-" + token

    def docker(*args, check=True):
        result = subprocess.run(
            ["docker", "--host", "unix:///var/run/docker.sock", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode:
            # Docker errors can include environment values; retain only the operation.
            raise RuntimeError("Disposable Docker operation failed: " + args[0])
        return result

    (ROOT / ".tools").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="ci-python-", dir=ROOT / ".tools", delete=False
    ) as directory:
        work = Path(directory)
        publication = PublishedNetwork(network, work / "network.json")
        pg_password, redis_password = secrets.token_hex(32), secrets.token_hex(32)
        pg_env, redis_env = work / "postgres.env", work / "redis.env"
        pg_env.write_text(
            f"POSTGRES_USER=ttcp_ci\nPOSTGRES_DB=ttcp_ci_{token}\nPOSTGRES_PASSWORD={pg_password}\n"
        )
        redis_env.write_text(f"REDIS_PASSWORD={redis_password}\n")
        pg_env.chmod(0o600)
        redis_env.chmod(0o600)
        try:
            publication.create()
            postgres_port = available_loopback_port()
            docker(
                "run",
                "-d",
                "--name",
                postgres,
                "--network",
                network,
                "--dns",
                "127.0.0.1",
                "--env-file",
                str(pg_env),
                "-p",
                f"127.0.0.1:{postgres_port}:5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
                "postgres:17.11-alpine3.23",
            )
            redis_port = available_loopback_port()
            docker(
                "run",
                "-d",
                "--name",
                redis,
                "--network",
                network,
                "--dns",
                "127.0.0.1",
                "--env-file",
                str(redis_env),
                "-p",
                f"127.0.0.1:{redis_port}:6379",
                "--tmpfs",
                "/run/redis:rw,size=1m",
                "--tmpfs",
                "/data:rw,size=16m",
                "-v",
                f"{ROOT / 'infra/compose/redis'}:/usr/local/etc/redis:ro",
                "--entrypoint",
                "/bin/sh",
                "redis:7.4.11-alpine3.21",
                "/usr/local/etc/redis/start.sh",
            )
            for container, probe in (
                (
                    postgres,
                    ["pg_isready", "-h", "127.0.0.1", "-U", "ttcp_ci", "-d", "ttcp_ci_" + token],
                ),
                (redis, ["sh", "-c", 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping']),
            ):
                for _ in range(100):
                    if docker("exec", container, *probe, check=False).returncode == 0:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError("Disposable service did not become ready")

            def port(container, internal, expected):
                result = docker("port", container, internal, check=False)
                if result.returncode:
                    raise RuntimeError(
                        "Docker has no published loopback binding; container health is insufficient"
                    )
                actual = published_port(result.stdout)
                if actual != expected:
                    raise RuntimeError("Disposable service lost its explicit loopback binding")
                return str(actual)

            # Use the exact PostgreSQL 17 tools shipped in the disposable server.
            # Forward secrets as environment, never command arguments or plaintext files.
            bin_dir = work / "bin"
            bin_dir.mkdir()
            for tool in ("pg_dump", "pg_restore"):
                wrapper = bin_dir / tool
                wrapper.write_text(
                    "#!/bin/sh\nexec docker --host unix:///var/run/docker.sock exec -i "
                    "-e PGDATABASE -e PGUSER -e PGPASSWORD -e PGSSLMODE "
                    "-e PGHOST=127.0.0.1 -e PGPORT=5432 "
                    f'{postgres} {tool} "$@"\n'
                )
                wrapper.chmod(0o700)
            env.update(
                TTCP_CI="1",
                TTCP_TEST_POSTGRES="1",
                TTCP_TEST_REDIS="1",
                TTCP_TEST_BACKUP_ROUNDTRIP="1",
                TTCP_POSTGRES_HOST="127.0.0.1",
                TTCP_POSTGRES_PORT=port(postgres, "5432/tcp", postgres_port),
                TTCP_POSTGRES_DB="ttcp_ci_" + token,
                TTCP_POSTGRES_USER="ttcp_ci",
                TTCP_POSTGRES_PASSWORD=pg_password,
                TTCP_REDIS_HOST="127.0.0.1",
                TTCP_REDIS_PORT=port(redis, "6379/tcp", redis_port),
                TTCP_REDIS_PASSWORD=redis_password,
                TTCP_TEST_REDIS_CONTAINER=redis,
                PATH=str(bin_dir) + os.pathsep + env["PATH"],
            )
            wait_tcp(int(env["TTCP_POSTGRES_PORT"]), "PostgreSQL")
            wait_tcp(int(env["TTCP_REDIS_PORT"]), "Redis")
            asyncio.run(verify_dependencies(env))
            print("PASS: published loopback PostgreSQL/Redis authenticated readiness")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "--strict-markers",
                    "--ignore=tests/integration/test_backup_release.py",
                ],
                env=env,
                cwd=ROOT,
                check=True,
            )
            # Dedicated real S3 suite is mandatory, rather than skipped by the main suite.
            subprocess.run(
                [
                    sys.executable,
                    "scripts/ci/backup_validation.py",
                    "--directory",
                    str(work / "backup"),
                ],
                env=env,
                cwd=ROOT,
                check=True,
            )
        finally:
            docker("rm", "-f", "-v", redis, postgres, check=False)
            publication.close()
            shutil.rmtree(work)


if __name__ == "__main__":
    main()
