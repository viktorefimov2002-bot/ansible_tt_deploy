import os

import pytest

from apps.shared.config import Settings
from tests.disposable import validate_ci_environment


def pytest_sessionstart(session):
    session.config._ttcp_ci = os.environ.get("TTCP_CI") == "1"
    session.config._ttcp_skipped = set()
    if session.config._ttcp_ci:
        try:
            validate_ci_environment(os.environ)
        except ValueError as error:
            raise pytest.UsageError(str(error)) from None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and item.config._ttcp_ci:
        item.config._ttcp_skipped.add(item.nodeid)


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    report = outcome.get_result()
    if report.skipped and collector.config._ttcp_ci:
        collector.config._ttcp_skipped.add(collector.nodeid)


def pytest_sessionfinish(session, exitstatus):
    skipped = session.config._ttcp_skipped
    if session.config._ttcp_ci and skipped:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        if reporter:
            reporter.write_sep("=", "CI forbids skipped release checks", red=True)
            for nodeid in sorted(skipped):
                reporter.write_line(nodeid, red=True)


@pytest.fixture
def settings(monkeypatch):
    # Disposable test-only values; no operator environment leaks into tests.
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
