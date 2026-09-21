"""Exercise real entry points and drivers, without requiring live services."""

import json
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("service", ["api", "worker"])
@pytest.mark.parametrize("failure", ["configuration", "connectivity"])
def test_entrypoint_fails_clearly_without_leaking_secrets(service, failure):
    env = {key: value for key, value in os.environ.items() if not key.startswith("TTCP_")}
    secret = "test-only:password@must-not-appear"
    if failure == "connectivity":
        env.update(
            {
                "TTCP_POSTGRES_HOST": "127.0.0.1",
                "TTCP_POSTGRES_PORT": "1",
                "TTCP_POSTGRES_DB": "test",
                "TTCP_POSTGRES_USER": "test",
                "TTCP_POSTGRES_PASSWORD": secret,
                "TTCP_REDIS_HOST": "127.0.0.1",
                "TTCP_REDIS_PORT": "1",
                "TTCP_REDIS_PASSWORD": secret,
                "TTCP_DEPENDENCY_TIMEOUT": "0.1",
            }
        )
    else:
        env["TTCP_POSTGRES_PORT"] = secret
    result = subprocess.run(
        [sys.executable, "-m", f"apps.{service}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert events and all(event["service"] == service for event in events)
    if failure == "configuration":
        assert "TTCP_POSTGRES_PORT" in result.stdout
        assert "TTCP_REDIS_PASSWORD" in result.stdout
    else:
        assert "dependency_unavailable" in result.stdout
        assert "postgres" in result.stdout and "redis" in result.stdout


@pytest.mark.parametrize("failure", ["configuration", "connectivity"])
def test_migration_cli_failure_redacts_credentials_and_does_not_require_redis(failure):
    env = {key: value for key, value in os.environ.items() if not key.startswith("TTCP_")}
    secret = "test-only:%migration@password"
    if failure == "configuration":
        env["TTCP_POSTGRES_PORT"] = secret
    else:
        env.update(
            {
                "TTCP_POSTGRES_HOST": "127.0.0.1",
                "TTCP_POSTGRES_PORT": "1",
                "TTCP_POSTGRES_DB": "test",
                "TTCP_POSTGRES_USER": "test",
                "TTCP_POSTGRES_PASSWORD": secret,
                "TTCP_DEPENDENCY_TIMEOUT": "0.1",
            }
        )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    assert secret not in output
    assert "REDIS" not in output
    assert "Traceback" not in output
