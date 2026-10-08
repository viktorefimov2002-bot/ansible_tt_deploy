"""Published backup storage must be reachable and isolated before the drill starts."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.ci import backup_validation


def runner(monkeypatch, tmp_path, *, readiness_failure=False, cleanup_failure=False):
    work = tmp_path / "backup"
    monkeypatch.setattr("sys.argv", ["backup_validation.py", "--directory", str(work)])
    monkeypatch.setenv("TTCP_CI", "1")
    monkeypatch.setenv("TTCP_POSTGRES_HOST", "127.0.0.1")
    monkeypatch.setenv("TTCP_POSTGRES_DB", "ttcp_ci_0123456789abcdef")
    monkeypatch.setenv("DOCKER_HOST", "tcp://operator.invalid:2375")
    monkeypatch.setenv("COMPOSE_FILE", "/operator/compose.yaml")
    monkeypatch.setenv("MINIO_ROOT_PASSWORD", "operator-secret")
    events = []

    class Network:
        def __init__(self, name, state):
            assert name.startswith("ttcp-ci-backup-")
            assert state == work / "network.json"
            self.name = name
            self.state = state

        def create(self):
            self.state.write_text("disposable firewall ownership")
            events.append("isolate")

        def close(self):
            assert work.is_dir()  # Keep firewall state until the network cleanup completes.
            events.append("network-close")
            if cleanup_failure:
                raise RuntimeError("Disposable containers remain attached")
            self.state.unlink()

    def execute(command, *, env, check, cwd=None):
        assert "DOCKER_HOST" not in env and "COMPOSE_FILE" not in env
        assert env["MINIO_ROOT_PASSWORD"] != "operator-secret"
        if "pytest" in command:
            assert events[-1] == "setup"
            events.append("tests")
            return SimpleNamespace(returncode=0)
        assert command[:4] == ["docker", "--host", "unix:///var/run/docker.sock", "compose"]
        assert Path(command[5]) == work / "compose.env"
        assert Path(command[5]).read_text() == ""
        assert env["TTCP_CI_BACKUP_NETWORK"].startswith("ttcp-ci-backup-")
        if "config" in command:
            events.append("config")
        elif "up" in command:
            assert events[-1] == "isolate"
            events.append("up")
        elif "run" in command:
            assert events[-1] == "reachable"
            events.append("setup")
        elif "down" in command:
            events.append("down")
        return SimpleNamespace(returncode=0)

    def reachable(port, hostname, ca_file, *, path):
        assert events[-1] == "up"
        assert (port, hostname, path) == (19000, "localhost", "/minio/health/ready")
        assert ca_file == work / "certs/ca.crt" and ca_file.is_file()
        if readiness_failure:
            raise RuntimeError("Published HTTPS storage endpoint is unreachable")
        events.append("reachable")

    monkeypatch.setattr(backup_validation, "PublishedNetwork", Network)
    monkeypatch.setattr(backup_validation, "wait_https", reachable)
    monkeypatch.setattr(backup_validation.subprocess, "run", execute)
    return work, events


def test_published_https_failure_stops_setup_and_tests_and_cleans_up(monkeypatch, tmp_path):
    work, events = runner(monkeypatch, tmp_path, readiness_failure=True)
    with pytest.raises(RuntimeError, match="unreachable"):
        backup_validation.main()
    assert events == ["config", "isolate", "up", "down", "network-close"]
    assert not work.exists()


def test_backup_runner_isolates_before_start_and_probes_before_setup(monkeypatch, tmp_path):
    work, events = runner(monkeypatch, tmp_path)
    assert backup_validation.main() == 0
    assert events == [
        "config",
        "isolate",
        "up",
        "reachable",
        "setup",
        "tests",
        "down",
        "network-close",
    ]
    assert not work.exists()
    assert (
        os.environ["MINIO_ROOT_PASSWORD"] == "operator-secret"
    )  # Parent environment is untouched.


def test_failed_network_cleanup_retains_ownership_record(monkeypatch, tmp_path):
    work, events = runner(monkeypatch, tmp_path, cleanup_failure=True)
    with pytest.raises(RuntimeError, match="remain attached"):
        backup_validation.main()
    assert events[-2:] == ["down", "network-close"]
    assert (work / "network.json").read_text() == "disposable firewall ownership"
