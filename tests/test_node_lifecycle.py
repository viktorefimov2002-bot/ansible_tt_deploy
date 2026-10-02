import importlib.util
import io
import json
import stat
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("node_lifecycle", ROOT / "scripts/node_lifecycle.py")
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "shell", "command": "id"},
        {"operation": "server.restart", "command": "id"},
        {"operation": "server.update", "version": "latest"},
        {"operation": "server.update", "version": "1.2.3", "url": "https://evil.test"},
        {"operation": "server.deploy", "version": "1.2.3", "domain": "../etc", "acme_http": False},
        {"operation": "server.deploy", "version": "1.2.3", "domain": "vpn.test", "acme_http": True},
        {
            "operation": "server.deploy",
            "version": "1.2.3",
            "domain": "vpn.test",
            "acme_email": "--hook=x",
            "acme_http": True,
        },
        {"operation": "server.uninstall", "path": "/"},
        {"operation": "server.uninstall", "acme_http": "false"},
    ],
)
def test_root_boundary_rejects_unallowlisted_inputs(payload):
    with pytest.raises(node.Rejected):
        node.validate(payload)


def test_archive_reads_only_regular_fixed_binary_member(monkeypatch):
    monkeypatch.setattr(node.platform, "machine", lambda: "x86_64")

    def archive(kind):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as package:
            member = tarfile.TarInfo("trusttunnel-v1.2.3-linux-x86_64/trusttunnel_endpoint")
            member.size = 6 if kind == "file" else 0
            member.type = tarfile.REGTYPE if kind == "file" else tarfile.SYMTYPE
            member.linkname = "/etc/shadow"
            package.addfile(member, io.BytesIO(b"binary") if kind == "file" else None)
        return buffer.getvalue()

    class Response(io.BytesIO):
        url = "https://release-assets.githubusercontent.com/asset"

    def download(url, timeout):
        assert (
            url
            == "https://github.com/TrustTunnel/TrustTunnel/releases/download/v1.2.3/trusttunnel-v1.2.3-linux-x86_64.tar.gz"
        )
        return Response(archive(kind))

    monkeypatch.setattr(node.urllib.request, "urlopen", download)
    kind = "file"
    assert node.release_binary("1.2.3") == b"binary"
    kind = "symlink"
    with pytest.raises(node.Rejected):
        node.release_binary("1.2.3")


@pytest.fixture
def workload(tmp_path, monkeypatch):
    for name, suffix in (
        ("DIRECTORY", "opt/trusttunnel"),
        ("UNIT", "etc/systemd/system/trusttunnel.service"),
        ("STATE", "etc/ttcp-bootstrap/workload.json"),
        ("HOOK", "etc/letsencrypt/renewal-hooks/deploy/ttcp-trusttunnel"),
        ("CERTIFICATES", "etc/letsencrypt/live"),
    ):
        monkeypatch.setattr(node, name, tmp_path / suffix)
    node.DIRECTORY.parent.mkdir(parents=True)
    monkeypatch.setattr(node, "trusted", lambda path: None)  # Windows has no Unix root uid/modes.
    monkeypatch.setattr(node.os, "fchmod", lambda *args: None, raising=False)
    monkeypatch.setattr(
        node, "TEMPLATES", ROOT / "automation/ansible/roles/trusttunnel_endpoint/templates"
    )
    commands, downloads = [], []
    monkeypatch.setattr(node, "command", lambda *args, **kwargs: commands.append(args))

    def binary(version):
        downloads.append(version)
        return version.encode()

    monkeypatch.setattr(node, "release_binary", binary)
    monkeypatch.setattr(node, "version", lambda executable: executable.read_text())
    monkeypatch.setattr(
        node, "healthy", lambda version: {"installed_version": version, "active": True}
    )
    cert = node.CERTIFICATES / "vpn.example.org"
    cert.mkdir(parents=True)
    for name in ("fullchain.pem", "privkey.pem"):
        (cert / name).write_text("test-only")
    request = {
        "operation": "server.deploy",
        "version": "1.2.3",
        "domain": "vpn.example.org",
        "acme_http": False,
    }
    return request, commands, downloads


def test_deploy_replay_update_preserves_config_uninstall_keeps_management(workload, tmp_path):
    request, commands, downloads = workload
    management = tmp_path / "etc/ssh/ttcp_authorized_keys"
    management.parent.mkdir(parents=True)
    management.write_text("test-only-public-key")
    assert node.apply(request)["active"]
    credentials = node.DIRECTORY / "credentials.toml"
    credentials.write_text("test-only-existing-credentials")
    node.apply(request)
    assert downloads == ["1.2.3"]
    assert credentials.read_text() == "test-only-existing-credentials"
    snapshot = (node.DIRECTORY / "vpn.toml").read_text()
    assert (
        node.apply({"operation": "server.update", "version": "1.2.4"})["installed_version"]
        == "1.2.4"
    )
    assert (node.DIRECTORY / "vpn.toml").read_text() == snapshot
    assert credentials.read_text() == "test-only-existing-credentials"
    assert node.apply({"operation": "server.uninstall"}) == {
        "installed_version": None,
        "active": False,
    }
    assert not node.DIRECTORY.exists() and not node.UNIT.exists()
    assert management.read_text() == "test-only-public-key"
    assert (node.CERTIFICATES / "vpn.example.org/fullchain.pem").exists()
    assert node.apply({"operation": "server.uninstall"})["active"] is False
    node.apply(request)
    assert node.DIRECTORY.exists() and management.exists()


def test_partial_install_and_health_failure_retry_converges(workload, monkeypatch):
    request, _, downloads = workload
    original = node.render
    monkeypatch.setattr(node, "render", lambda _: (_ for _ in ()).throw(node.Rejected()))
    with pytest.raises(node.Rejected):
        node.apply(request)
    assert node.STATE.exists()
    monkeypatch.setattr(node, "render", original)
    assert node.apply(request)["active"]
    assert downloads == ["1.2.3"]
    monkeypatch.setattr(node, "healthy", lambda _: (_ for _ in ()).throw(node.Rejected()))
    with pytest.raises(node.Rejected):
        node.apply({"operation": "server.update", "version": "1.2.4"})
    monkeypatch.setattr(
        node, "healthy", lambda version: {"installed_version": version, "active": True}
    )
    assert node.apply({"operation": "server.update", "version": "1.2.4"})["active"]
    assert downloads == ["1.2.3", "1.2.4"]


def test_foreign_installation_is_not_adopted_or_deleted(workload):
    request, _, _ = workload
    node.DIRECTORY.mkdir(parents=True)
    (node.DIRECTORY / "foreign").write_text("keep")
    for operation in (request, {"operation": "server.uninstall"}):
        with pytest.raises(node.Rejected):
            node.apply(operation)
    assert (node.DIRECTORY / "foreign").read_text() == "keep"


def test_trust_rejects_unprivileged_or_writable_ancestors(tmp_path, monkeypatch):
    original = Path.stat

    def unsafe(path, *args, **kwargs):
        original(path, *args, **kwargs)
        return SimpleNamespace(st_uid=123 if path == tmp_path else 0, st_mode=stat.S_IFDIR | 0o755)

    monkeypatch.setattr(Path, "stat", unsafe)
    with pytest.raises(node.Rejected):
        node.trusted(tmp_path / "file")


def test_concurrent_node_operation_fails_before_mutation(tmp_path, monkeypatch):
    lock = tmp_path / "ttcp-workload.lock"
    monkeypatch.setattr(node, "LOCK", lock)
    monkeypatch.setattr(node.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(node.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(
        node.os, "fstat", lambda _: SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o600)
    )
    monkeypatch.setattr(node.sys, "argv", ["ttcp-node-lifecycle"])
    monkeypatch.setattr(
        node.sys, "stdin", io.StringIO(json.dumps({"operation": "server.uninstall"}))
    )

    def occupied(fd, flags):
        assert flags == 6  # LOCK_EX | LOCK_NB; never queue a stale mutation on-node.
        raise BlockingIOError

    monkeypatch.setitem(
        node.sys.modules, "fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=occupied)
    )

    def unexpected(_):
        pytest.fail("Concurrent mutation reached workload application")

    monkeypatch.setattr(node, "apply", unexpected)
    with pytest.raises(BlockingIOError):
        node.main()
