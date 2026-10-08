"""Mandatory disposable HTTPS object-storage drill; no operator config is accepted."""

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra/compose/compose.ci-backup.yaml"


def prepare(directory: Path, port: int) -> dict[str, str]:
    """Only create fresh artifacts so operator secrets can never be overwritten."""
    directory = directory.resolve()
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    certs = directory / "certs"
    certs.mkdir(mode=0o700)
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TTCP disposable CI CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.DNSName("minio"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    (certs / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (certs / "public.crt").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file = certs / "private.key"
    key_file.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_file.chmod(0o600)
    access, secret = "ci" + secrets.token_hex(12), secrets.token_hex(32)
    protected = directory / "service.json"
    protected.write_text(
        json.dumps(
            {
                "endpoint": f"https://127.0.0.1:{port}",
                "access_key": access,
                "secret_key": secret,
                "ca_file": str(certs / "ca.crt"),
            }
        ),
        encoding="utf-8",
    )
    protected.chmod(0o600)
    # Explicitly disable Compose's automatic operator .env discovery.
    (directory / "compose.env").write_text("", encoding="utf-8")
    return {
        "MINIO_ROOT_USER": access,
        "MINIO_ROOT_PASSWORD": secret,
        "TTCP_CI_BACKUP_CERTS_PATH": str(certs),
        "TTCP_CI_BACKUP_PORT": str(port),
        "TTCP_CI_BACKUP_DIR": str(directory),
        "TTCP_TEST_BACKUP_STORAGE": "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19000)
    args = parser.parse_args()
    if (
        os.environ.get("TTCP_CI") != "1"
        or os.environ.get("TTCP_POSTGRES_HOST") not in {"127.0.0.1", "localhost"}
        or not os.environ.get("TTCP_POSTGRES_DB", "").startswith("ttcp_ci_")
        or not 1024 <= args.port <= 65535
    ):
        parser.error("Requires TTCP_CI=1 and an explicitly disposable loopback ttcp_ci_ database")
    env = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COMPOSE_", "DOCKER_", "MINIO_"))
    } | prepare(args.directory, args.port)
    project = "ttcp-ci-backup-" + uuid4().hex[:12]
    compose = [
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "--env-file",
        str(Path(env["TTCP_CI_BACKUP_DIR"]) / "compose.env"),
        "-p",
        project,
        "-f",
        str(COMPOSE),
    ]
    try:
        subprocess.run([*compose, "config", "--quiet"], env=env, check=True)
        subprocess.run([*compose, "up", "-d", "--wait", "minio"], env=env, check=True)
        subprocess.run([*compose, "run", "--rm", "setup"], env=env, check=True)
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/integration/test_backup_release.py"],
            cwd=ROOT,
            env=env,
            check=False,
        ).returncode
    finally:
        # Exact unique project created above; never an operator Compose project.
        try:
            subprocess.run([*compose, "down", "--volumes", "--remove-orphans"], env=env, check=True)
        finally:
            # prepare() exclusively created this resolved directory; no preexisting tree is removed.
            shutil.rmtree(Path(env["TTCP_CI_BACKUP_DIR"]))


if __name__ == "__main__":
    raise SystemExit(main())
