import pytest

from apps.shared.config import Settings


@pytest.fixture
def settings(monkeypatch):
    # Disposable test-only values; no operator environment leaks into tests.
    import os

    for name in os.environ:
        if name.startswith("TTCP_"):
            monkeypatch.delenv(name)
    return Settings(
        postgres_host="localhost",
        postgres_db="test",
        postgres_user="test",
        postgres_password="test-only-pg",
        redis_host="localhost",
        redis_password="test-only-redis",
        dependency_timeout=0.1,
        worker_check_interval=1,
    )
