"""Check the published loopback TLS entrypoint before running the browser journey."""

import argparse
import sys
from pathlib import Path

# Support both direct Linux runner invocation and ordinary package imports.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.networking import wait_https  # noqa: E402

HOSTS = {"admin.localhost": "admin", "vpn.localhost": "client"}


def probe(port: int, tls: Path):
    for host, role in HOSTS.items():
        certificate = tls / role / "fullchain.pem"
        title = "TrustTunnel Admin" if role == "admin" else "TrustTunnel — подключение"
        wait_https(port, host, certificate, body_marker=f"<title>{title}</title>".encode())
        own = "/api/auth/me" if role == "admin" else "/api/client/me"
        opposite = "/api/client/me" if role == "admin" else "/api/auth/me"
        for path, expected in ((own, 401), (opposite, 404)):
            wait_https(port, host, certificate, path=path, status=expected)
        print(f"PASS: published HTTPS static/API origin boundary for {host}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--tls-dir", type=Path, required=True)
    args = parser.parse_args()
    probe(args.port, args.tls_dir)


if __name__ == "__main__":
    main()
