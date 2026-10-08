"""Probe real native production NGINX; its anonymous upstream is an HTTP fixture."""

import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import NameOID

from scripts.ci.e2e_probe import probe
from tests.test_client_edge import edge  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("edge", ["production"], indirect=True)
def test_e2e_entrypoint_probe_verifies_actual_nginx_tls_static_and_api_isolation(edge, tmp_path):  # noqa: F811
    port, received = edge
    key = serialization.load_pem_private_key((tmp_path / "key.pem").read_bytes(), password=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "admin.localhost")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("admin.localhost"), x509.DNSName("vpn.localhost")]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
        .public_bytes(serialization.Encoding.PEM)
    )
    (tmp_path / "cert.pem").write_bytes(certificate)
    configuration = (tmp_path / "nginx.conf").read_text()
    for role, app in (("admin", "admin-web"), ("client", "client-web")):
        directory = tmp_path / role
        directory.mkdir()
        (directory / "fullchain.pem").write_bytes(certificate)
        shutil.copyfile(ROOT / "apps" / app / "index.html", directory / "index.html")
        configuration = configuration.replace(
            f"root /usr/share/nginx/html/{role};", f"root {directory.as_posix()};"
        )
    (tmp_path / "nginx.conf").write_text(configuration)
    executable = os.environ.get("TTCP_TEST_NGINX") or shutil.which("nginx")
    subprocess.run(
        [executable, "-p", tmp_path.as_posix() + "/", "-c", "nginx.conf", "-s", "reload"],
        capture_output=True,
        check=True,
        timeout=10,
    )
    probe(port, tmp_path)
    # Only the two own-origin anonymous checks reach the upstream. Both opposite
    # APIs are denied by the actual production NGINX routing policy.
    assert len(received) == 2
    assert {headers["Host"] for headers in received} == {
        f"admin.localhost:{port}",
        f"vpn.localhost:{port}",
    }
