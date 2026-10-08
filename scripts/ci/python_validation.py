"""Run Linux integration gates against newly created, loopback-only containers."""

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
            ["docker", *args], env=env, capture_output=True, text=True, check=False
        )
        if check and result.returncode:
            # Docker errors can include environment values; retain only the operation.
            raise RuntimeError("Disposable Docker operation failed: " + args[0])
        return result

    (ROOT / ".tools").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ci-python-", dir=ROOT / ".tools") as directory:
        work = Path(directory)
        pg_password, redis_password = secrets.token_hex(32), secrets.token_hex(32)
        pg_env, redis_env = work / "postgres.env", work / "redis.env"
        pg_env.write_text(
            f"POSTGRES_USER=ttcp_ci\nPOSTGRES_DB=ttcp_ci_{token}\nPOSTGRES_PASSWORD={pg_password}\n"
        )
        redis_env.write_text(f"REDIS_PASSWORD={redis_password}\n")
        pg_env.chmod(0o600)
        redis_env.chmod(0o600)
        try:
            docker("network", "create", "--internal", network)
            docker(
                "run",
                "-d",
                "--name",
                postgres,
                "--network",
                network,
                "--env-file",
                str(pg_env),
                "-p",
                "127.0.0.1::5432",
                "--tmpfs",
                "/var/lib/postgresql/data:rw,size=512m",
                "postgres:17.11-alpine3.23",
            )
            docker(
                "run",
                "-d",
                "--name",
                redis,
                "--network",
                network,
                "--env-file",
                str(redis_env),
                "-p",
                "127.0.0.1::6379",
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

            def port(container, internal):
                value = docker("port", container, internal).stdout.strip()
                if not value.startswith("127.0.0.1:") or "\n" in value:
                    raise RuntimeError("Disposable port must be loopback only")
                return value.rsplit(":", 1)[1]

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
                TTCP_POSTGRES_PORT=port(postgres, "5432/tcp"),
                TTCP_POSTGRES_DB="ttcp_ci_" + token,
                TTCP_POSTGRES_USER="ttcp_ci",
                TTCP_POSTGRES_PASSWORD=pg_password,
                TTCP_REDIS_HOST="127.0.0.1",
                TTCP_REDIS_PORT=port(redis, "6379/tcp"),
                TTCP_REDIS_PASSWORD=redis_password,
                TTCP_TEST_REDIS_CONTAINER=redis,
                PATH=str(bin_dir) + os.pathsep + env["PATH"],
            )
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
            docker("network", "rm", network, check=False)


if __name__ == "__main__":
    main()
