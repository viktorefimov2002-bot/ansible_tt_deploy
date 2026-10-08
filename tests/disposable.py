"""Fail closed before CI opens a connection or mutates disposable services."""

import re
from collections.abc import Mapping

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
REDIS_CONTAINER = re.compile(r"ttcp-ci-redis-[a-z0-9]{8,64}")


def validate_ci_environment(environment: Mapping[str, str]) -> None:
    for name in ("TTCP_TEST_POSTGRES", "TTCP_TEST_REDIS"):
        if environment.get(name) != "1":
            raise ValueError(f"CI requires {name}=1; integration checks cannot be skipped")
    for name in ("TTCP_POSTGRES_HOST", "TTCP_REDIS_HOST"):
        if environment.get(name) not in LOCAL_HOSTS:
            raise ValueError(f"CI requires a loopback {name}; external targets are forbidden")
    if not re.fullmatch(r"ttcp_ci_[a-z0-9]{8,64}", environment.get("TTCP_POSTGRES_DB", "")):
        raise ValueError("CI requires a generated ttcp_ci_<random> PostgreSQL database")
    for name in ("TTCP_POSTGRES_PASSWORD", "TTCP_REDIS_PASSWORD"):
        if len(environment.get(name, "")) < 32:
            raise ValueError(f"CI requires a generated disposable {name}")
    if not REDIS_CONTAINER.fullmatch(environment.get("TTCP_TEST_REDIS_CONTAINER", "")):
        raise ValueError("CI requires its generated ttcp-ci-redis-<random> restart target")
