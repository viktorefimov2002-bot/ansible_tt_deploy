"""Real loopback/TLS probes and release topology regressions, without Docker mocks."""

import json
import socket
import ssl
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts.ci import network_smoke
from scripts.ci.networking import LABEL, PublishedNetwork, published_port, wait_https, wait_tcp

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("output", ["127.0.0.1:54321", "127.0.0.1:54321\n"])
def test_published_port_accepts_one_explicit_loopback_binding(output):
    assert published_port(output) == 54321


@pytest.mark.parametrize(
    "output",
    [
        "",
        "0.0.0.0:54321\n",
        "[::]:54321\n",
        "192.0.2.1:54321\n",
        "localhost:54321\n",
        "127.0.0.1:54321\n[::1]:54321\n",
        "127.0.0.1:0\n",
        "127.0.0.1:65536\n",
        "127.0.0.1:not-a-port\n",
    ],
)
def test_published_port_refuses_absent_public_or_ambiguous_bindings(output):
    with pytest.raises((ValueError, RuntimeError)):
        published_port(output)


def test_tcp_probe_connects_to_a_real_loopback_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(1)
        wait_tcp(listener.getsockname()[1], "disposable TCP fixture", timeout=1)
        connection, address = listener.accept()
        with connection:
            assert address[0] == "127.0.0.1"


def test_tcp_probe_fails_when_port_is_bound_but_not_listening():
    with socket.socket() as guard:
        guard.bind(("127.0.0.1", 0))
        with pytest.raises((RuntimeError, TimeoutError)):
            wait_tcp(guard.getsockname()[1], "unreachable TCP fixture", timeout=0.03)


def test_https_probe_refuses_external_target_before_dns_tls_or_file_access(tmp_path, monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: pytest.fail("No DNS/connection attempt is permitted"),
    )
    with pytest.raises(ValueError):
        wait_https(443, "production.example.test", tmp_path / "nonexistent-ca.pem", timeout=0.03)


@contextmanager
def https_fixture(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "admin.localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("admin.localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_file, key_file = tmp_path / "certificate.pem", tmp_path / "private.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    seen = {"sni": [], "host": [], "path": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"].append(self.headers["Host"])
            seen["path"].append(self.path)
            body = b"disposable fixture ready"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    context.set_servername_callback(lambda _, hostname, __: seen["sni"].append(hostname))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, cert_file, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_https_probe_uses_loopback_with_real_tls_sni_host_and_response(tmp_path, monkeypatch):
    original = socket.getaddrinfo

    def local_only(host, *args, **kwargs):
        assert host == "127.0.0.1", "TLS probe must never resolve or contact an external hostname"
        return original(host, *args, **kwargs)

    with https_fixture(tmp_path) as (port, ca_file, seen):
        monkeypatch.setattr(socket, "getaddrinfo", local_only)
        wait_https(
            port,
            "admin.localhost",
            ca_file,
            path="/healthz",
            body_marker=b"fixture ready",
            timeout=1,
        )
    assert seen["sni"] == ["admin.localhost"]
    assert seen["host"] == [f"admin.localhost:{port}"]
    assert seen["path"] == ["/healthz"]


@pytest.mark.parametrize("failure", ["hostname", "status", "body"])
def test_https_probe_rejects_wrong_origin_status_or_body(tmp_path, failure):
    with https_fixture(tmp_path) as (port, ca_file, _):
        with pytest.raises((RuntimeError, TimeoutError)):
            wait_https(
                port,
                "vpn.localhost" if failure == "hostname" else "admin.localhost",
                ca_file,
                status=204 if failure == "status" else 200,
                body_marker=b"absent-marker" if failure == "body" else None,
                timeout=0.03,
            )


@pytest.mark.parametrize("application", ["e2e", "backup"])
def test_ci_topology_exposes_only_guarded_loopback_services_and_denies_external_dns(application):
    model = yaml.safe_load((ROOT / "infra/compose" / f"compose.ci-{application}.yaml").read_text())
    publication = "nginx" if application == "e2e" else "minio"
    guarded = "host_ingress" if application == "e2e" else "backup_test"
    definition = model["networks"][guarded]
    assert definition["external"] is True
    assert f"TTCP_CI_{application.upper()}_NETWORK" in definition["name"]
    assert not definition.get("internal"), "Host publication cannot use an internal bridge"
    for name, service in model["services"].items():
        assert not service.get("network_mode")
        ports = service.get("ports", [])
        assert bool(ports) == (name == publication)
        assert all(binding.startswith("127.0.0.1:") for binding in ports)
        if guarded in service["networks"]:
            assert service["dns"] == ["127.0.0.1"]
        if application == "e2e":
            assert model["networks"]["isolated"]["internal"] is True
            assert service["networks"] == (
                ["isolated", guarded] if name == publication else ["isolated"]
            )
        else:
            assert service["networks"] == [guarded]


@pytest.mark.parametrize("name", ["ttcp-dev", "production", "ttcp-ci-../../elsewhere"])
def test_network_ownership_refuses_uncontrolled_names(tmp_path, name):
    with pytest.raises(ValueError):
        PublishedNetwork(name, tmp_path / "state.json")


@pytest.mark.parametrize("version", ["27.5.0", "unexpected-version"])
def test_network_creation_refuses_unsafe_engine_before_network_or_firewall_mutation(
    tmp_path, monkeypatch, version
):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        assert args == ("version", "--format", "{{.Server.Version}}")
        return subprocess.CompletedProcess(args, 0, stdout=version)

    monkeypatch.setattr(network, "docker", docker)
    monkeypatch.setattr(network, "firewall", lambda *_, **__: pytest.fail("No firewall mutation"))
    with pytest.raises(RuntimeError):
        network.create()
    assert calls == [("version", "--format", "{{.Server.Version}}")]
    assert not network.state.exists()


def test_network_creation_requires_firewall_backend_before_a_network_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")

    def docker(*args, **kwargs):
        assert args == ("version", "--format", "{{.Server.Version}}")
        return subprocess.CompletedProcess(args, 0, stdout="28.5.0")

    def denied(*args, **kwargs):
        assert args == ("-S", "DOCKER-USER")
        raise RuntimeError("Firewall is unavailable")

    monkeypatch.setattr(network, "docker", docker)
    monkeypatch.setattr(network, "firewall", denied)
    with pytest.raises(RuntimeError, match="unavailable"):
        network.create()
    assert not network.state.exists()


def network_model(network):
    return {
        "Id": network.id,
        "Name": network.name,
        "Labels": {LABEL: network.token},
        "Driver": "bridge",
        "Internal": False,
        "EnableIPv6": False,
        "Options": {
            "com.docker.network.bridge.name": network.bridge,
            "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1",
            "com.docker.network.bridge.enable_ip_masquerade": "false",
            "com.docker.network.bridge.gateway_mode_ipv4": "nat",
        },
    }


@pytest.mark.parametrize("change", ["id", "name", "label", "internal", "ipv6", "binding", "masq"])
def test_network_cleanup_refuses_foreign_or_permissive_networks(tmp_path, monkeypatch, change):
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    model = network_model(network)
    if change == "id":
        model["Id"] = "b" * 64
    elif change == "name":
        model["Name"] = "ttcp-dev"
    elif change == "label":
        model["Labels"][LABEL] = "other-owner"
    elif change == "internal":
        model["Internal"] = True
    elif change == "ipv6":
        model["EnableIPv6"] = True
    elif change == "binding":
        model["Options"]["com.docker.network.bridge.host_binding_ipv4"] = "0.0.0.0"
    else:
        model["Options"]["com.docker.network.bridge.enable_ip_masquerade"] = "true"
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        assert args == ("network", "inspect", network.id)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps([model]))

    monkeypatch.setattr(network, "docker", docker)
    monkeypatch.setattr(network, "firewall", lambda *_, **__: pytest.fail("No firewall mutation"))
    with pytest.raises(RuntimeError):
        network.close()
    assert calls == [("network", "inspect", network.id)]


def test_attached_network_cleanup_retains_egress_rules_and_ownership(tmp_path, monkeypatch):
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    network.state.write_text("owned record")
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args[1] == "inspect":
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps([network_model(network)]))
        raise RuntimeError("Container remains attached")

    monkeypatch.setattr(network, "docker", docker)
    monkeypatch.setattr(
        network, "firewall", lambda *_, **__: pytest.fail("Rules must remain active")
    )
    with pytest.raises(RuntimeError, match="attached"):
        network.close()
    assert calls == [("network", "inspect", network.id), ("network", "rm", network.id)]
    assert network.state.exists() and network.id is not None


@pytest.mark.parametrize("change", ["version", "id", "token", "bridge"])
def test_cleanup_ownership_record_rejects_tampering_before_any_connection(
    tmp_path, monkeypatch, change
):
    monkeypatch.setattr(sys, "platform", "linux")
    record = {
        "version": 1,
        "name": "ttcp-ci-unit-12345678",
        "id": "a" * 64,
        "token": "a" * 16,
        "bridge": "ttci" + "a" * 10,
    }
    record[change] = 2 if change == "version" else "uncontrolled"
    path = tmp_path / "state.json"
    path.write_text(json.dumps(record))
    with pytest.raises(RuntimeError):
        PublishedNetwork.from_state(path)


def test_interrupted_rule_cleanup_persists_removed_phase_and_can_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    network.save_state()
    active_rules = {"DOCKER-USER", "INPUT"}
    calls = []

    def docker(*args, **kwargs):
        calls.append(args)
        if args == ("network", "inspect", network.id):
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps([network_model(network)]))
        assert args == ("network", "rm", network.id)
        return subprocess.CompletedProcess(args, 0, stdout=network.id)

    def interrupted_firewall(operation, chain, *rule, **kwargs):
        assert rule == tuple(dict(network.rules())[chain])
        assert json.loads(network.state.read_text())["removed"] is True
        if operation == "-C":
            return subprocess.CompletedProcess((operation, chain), int(chain not in active_rules))
        assert operation == "-D"
        if chain == "INPUT":
            raise RuntimeError("Firewall delete interrupted")
        active_rules.remove(chain)
        return subprocess.CompletedProcess((operation, chain), 0)

    monkeypatch.setattr(network, "docker", docker)
    monkeypatch.setattr(network, "firewall", interrupted_firewall)
    with pytest.raises(RuntimeError, match="interrupted"):
        network.close()
    assert calls == [("network", "inspect", network.id), ("network", "rm", network.id)]
    assert json.loads(network.state.read_text())["removed"] is True
    assert active_rules == {"INPUT"}

    restored = PublishedNetwork.from_state(network.state)
    assert restored.removed is True
    probes = []

    def no_bridge(args, **kwargs):
        assert args == ["ip", "link", "show", "dev", restored.bridge]
        assert kwargs == {"check": False}
        probes.append(args)
        return subprocess.CompletedProcess(args, 1)

    def resume_firewall(operation, chain, *rule, **kwargs):
        assert probes, "Verify the owned bridge is gone before changing rules"
        assert rule == tuple(dict(restored.rules())[chain])
        if operation == "-C":
            return subprocess.CompletedProcess((operation, chain), int(chain not in active_rules))
        assert operation == "-D"
        active_rules.remove(chain)
        return subprocess.CompletedProcess((operation, chain), 0)

    monkeypatch.setattr(restored, "run", no_bridge)
    monkeypatch.setattr(restored, "docker", lambda *_, **__: pytest.fail("Network already removed"))
    monkeypatch.setattr(restored, "firewall", resume_firewall)
    restored.close()
    assert probes == [["ip", "link", "show", "dev", restored.bridge]]
    assert not active_rules and not restored.state.exists() and restored.id is None


@pytest.mark.parametrize("inspection_result", [0, 2])
def test_removed_phase_refuses_reused_or_uninspectable_bridge(
    tmp_path, monkeypatch, inspection_result
):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    network.removed = True
    network.save_state()
    restored = PublishedNetwork.from_state(network.state)
    monkeypatch.setattr(
        restored,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, inspection_result),
    )
    monkeypatch.setattr(restored, "docker", lambda *_, **__: pytest.fail("No Docker mutation"))
    monkeypatch.setattr(restored, "firewall", lambda *_, **__: pytest.fail("No firewall mutation"))
    with pytest.raises(RuntimeError):
        restored.close()
    assert restored.state.exists() and restored.id is not None


@pytest.mark.parametrize("phase", ["true", None, 1, [], {}])
def test_cleanup_record_refuses_non_boolean_removed_phase(tmp_path, monkeypatch, phase):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    network.save_state()
    record = json.loads(network.state.read_text())
    record["removed"] = phase
    network.state.write_text(json.dumps(record))
    with pytest.raises(RuntimeError):
        PublishedNetwork.from_state(network.state)


def test_legacy_ownership_record_defaults_to_active_network(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    network = PublishedNetwork("ttcp-ci-unit-12345678", tmp_path / "state.json")
    network.id = "a" * 64
    network.save_state()
    record = json.loads(network.state.read_text())
    del record["removed"]
    network.state.write_text(json.dumps(record))
    restored = PublishedNetwork.from_state(network.state)
    assert restored.removed is False
    assert (restored.id, restored.token, restored.bridge) == (
        network.id,
        network.token,
        network.bridge,
    )


def test_network_drill_removes_its_fresh_directory_after_successful_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(network_smoke, "ROOT", tmp_path)
    with network_smoke.owned_directory() as work:
        (work / "redis.env").write_text("synthetic disposable fixture")
    assert not work.exists()


def test_network_drill_preserves_failed_cleanup_ownership_record(tmp_path, monkeypatch):
    monkeypatch.setattr(network_smoke, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        with network_smoke.owned_directory() as work:
            (work / "protected.json").write_text("disposable ownership record")
            raise RuntimeError("cleanup failed")
    assert work.parent == tmp_path / ".tools"
    assert (work / "protected.json").read_text() == "disposable ownership record"
