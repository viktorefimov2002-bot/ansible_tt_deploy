"""Published backup storage must be reachable and isolated before the drill starts."""

import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.ci import backup_validation


def runner(
    monkeypatch, tmp_path, *, readiness_failure=False, cleanup_failure=False, build_failure=False
):
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
            if self.state.exists():
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
        elif "build" in command:
            assert command[-4:] == ["build", "--pull", "minio", "setup"]
            assert events[-1] == "config"
            events.append("build")
            if build_failure:
                raise RuntimeError("Disposable image build failed")
        elif "up" in command:
            assert events[-1] == "isolate"
            assert command[-5:] == ["--wait", "--no-build", "--pull", "never", "minio"]
            events.append("up")
        elif "run" in command:
            assert events[-1] == "reachable"
            assert command[-5:] == ["--rm", "--no-deps", "--pull", "never", "setup"]
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
    assert events == ["config", "build", "isolate", "up", "down", "network-close"]
    assert not work.exists()


def test_backup_runner_isolates_before_start_and_probes_before_setup(monkeypatch, tmp_path):
    work, events = runner(monkeypatch, tmp_path)
    assert backup_validation.main() == 0
    assert events == [
        "config",
        "build",
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


def test_image_build_failure_stops_infrastructure_and_real_drill(monkeypatch, tmp_path):
    work, events = runner(monkeypatch, tmp_path, build_failure=True)
    with pytest.raises(RuntimeError, match="image build failed"):
        backup_validation.main()
    assert events == ["config", "build", "down", "network-close"]
    assert not work.exists()


def test_backup_images_use_isolated_checksummed_source_builds_without_registry_fallback():
    model = yaml.safe_load(backup_validation.COMPOSE.read_text())
    contexts = []
    for name, target in (("minio", "minio"), ("setup", "mc")):
        service = model["services"][name]
        context = (backup_validation.COMPOSE.parent / service["build"]["context"]).resolve()
        contexts.append(context)
        assert service["build"]["target"] == target
        assert service["image"].startswith("ttcp-ci-")
        assert ":RELEASE." in service["image"]
        assert not service["build"].get("args")  # No fixture/environment material enters builds.
        assert context == backup_validation.COMPOSE.parent / "ci-backup"
        assert (context / ".dockerignore").read_text().splitlines() == [
            "**",
            "!Dockerfile",
            "!.dockerignore",
        ]
    dockerfile = (contexts[0] / "Dockerfile").read_text()
    references = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    external = [reference for reference in references if ":" in reference]
    assert len(external) == 2
    assert all(re.fullmatch(r".+:[^@]+@sha256:[a-f0-9]{64}", ref) for ref in external)
    assert (
        len(
            re.findall(
                r"https://codeload.github.com/minio/(?:minio|mc)/tar.gz/[a-f0-9]{40}", dockerfile
            )
        )
        == 2
    )
    assert dockerfile.count("sha256sum -c -") == 2
