"""CI-only host publication with scoped egress denial and literal-loopback probes."""

import argparse
import http.client
import json
import os
import re
import secrets
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

NAMES = re.compile(r"ttcp-ci-[a-z0-9][a-z0-9-]{6,100}")
HOSTS = {"localhost", "admin.localhost", "vpn.localhost"}
LABEL = "ttcp.ci.published"


def available_loopback_port() -> int:
    """Request a kernel-selected but explicit Docker binding, stable across restart.

    A competing bind before container start must fail, never change the address.
    Docker's empty host port would instead be reallocated on every restart.
    """
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def published_port(output: str) -> int:
    match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", output.strip())
    if not match or not 1 <= int(match[1]) <= 65535:
        raise RuntimeError("Expected exactly one published IPv4 loopback TCP port")
    return int(match[1])


def wait_tcp(port: int, label: str, *, timeout: float = 15) -> None:
    if not 1 <= port <= 65535 or timeout <= 0:
        raise ValueError("Invalid published-port probe")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=min(timeout, 1)):
                return
        except OSError:
            time.sleep(min(0.1, timeout))
    raise RuntimeError(f"Published {label} port is unreachable on IPv4 loopback") from None


class LoopbackHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # Pin the destination independently of DNS, preserving TLS SNI and Host.
        raw = socket.create_connection(("127.0.0.1", self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def wait_https(
    port: int,
    hostname: str,
    ca_file: Path,
    *,
    path: str = "/",
    status: int = 200,
    body_marker: bytes | None = None,
    timeout: float = 15,
) -> None:
    if hostname not in HOSTS or not 1 <= port <= 65535 or timeout <= 0:
        raise ValueError("HTTPS probe permits only disposable loopback origins")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        raise ValueError("Invalid HTTPS probe path")
    context = ssl.create_default_context(cafile=str(ca_file))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connection = LoopbackHTTPS(hostname, port, context=context, timeout=min(timeout, 1))
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            body = response.read(64 * 1024)
            if response.status == status and (body_marker is None or body_marker in body):
                return
        except (OSError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(min(0.1, timeout))
    # Response bodies, authentication material and raw TLS/HTTP errors stay out of logs.
    raise RuntimeError(f"Published HTTPS origin {hostname} failed its readiness check") from None


class PublishedNetwork:
    """Own one bridge and two exact firewall rules; never alter global policies."""

    def __init__(self, name: str, state: Path):
        if not NAMES.fullmatch(name):
            raise ValueError("Only generated ttcp-ci network names are permitted")
        self.name = name
        self.state = Path(state)
        self.token = secrets.token_hex(8)
        self.bridge = "ttci" + self.token[:10]
        self.id = None
        self.removed = False

    def save_state(self):
        self.state.write_text(
            json.dumps(
                {
                    "version": 1,
                    "name": self.name,
                    "id": self.id,
                    "bridge": self.bridge,
                    "token": self.token,
                    "removed": self.removed,
                }
            )
        )
        self.state.chmod(0o600)

    def run(self, args, *, check=True):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DOCKER_", "COMPOSE_"))}
        result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
        if check and result.returncode:
            raise RuntimeError("Disposable network operation failed (Docker/iptables required)")
        return result

    def docker(self, *args, check=True):
        return self.run(["docker", "--host", "unix:///var/run/docker.sock", *args], check=check)

    def firewall(self, *args, check=True):
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        return self.run([*prefix, "iptables", "-w", "5", *args], check=check)

    def rules(self):
        yield (
            "DOCKER-USER",
            [
                "-i",
                self.bridge,
                "!",
                "-o",
                self.bridge,
                "-m",
                "conntrack",
                "--ctstate",
                "NEW",
                "-j",
                "DROP",
            ],
        )
        yield (
            "INPUT",
            [
                "-i",
                self.bridge,
                "-m",
                "conntrack",
                "--ctstate",
                "NEW",
                "-j",
                "DROP",
            ],
        )

    def create(self):
        if sys.platform != "linux":
            raise RuntimeError("Published CI networks require Linux and local Docker/iptables")
        if self.state.exists() or self.state.is_symlink():
            raise RuntimeError("Refusing to overwrite a network ownership record")
        version = self.docker("version", "--format", "{{.Server.Version}}").stdout.strip()
        if not re.match(r"^[0-9]+\.", version) or int(version.split(".")[0]) < 28:
            raise RuntimeError(
                "Docker Engine 28+ is required for IPv4 loopback publication isolation"
            )
        self.firewall("-S", "DOCKER-USER")  # No permissive fallback for a missing firewall backend.
        self.id = self.docker(
            "network",
            "create",
            "--driver",
            "bridge",
            "--ipv6=false",
            "--label",
            f"{LABEL}={self.token}",
            "--opt",
            f"com.docker.network.bridge.name={self.bridge}",
            "--opt",
            "com.docker.network.bridge.host_binding_ipv4=127.0.0.1",
            "--opt",
            "com.docker.network.bridge.enable_ip_masquerade=false",
            "--opt",
            "com.docker.network.bridge.gateway_mode_ipv4=nat",
            self.name,
        ).stdout.strip()
        if not re.fullmatch(r"[a-f0-9]{64}", self.id):
            raise RuntimeError("Docker did not return a valid disposable network ID")
        self.save_state()
        try:
            # Install before any container attaches. Responses to host-initiated
            # connections are ESTABLISHED; same-bridge peer traffic remains allowed.
            for chain, rule in self.rules():
                self.firewall("-I", chain, "1", *rule)
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self):
        model = json.loads(self.docker("network", "inspect", self.id).stdout)[0]
        if (
            model["Id"] != self.id
            or model["Name"] != self.name
            or model.get("Labels", {}).get(LABEL) != self.token
            or model["Driver"] != "bridge"
            or model["Internal"]
            or model["EnableIPv6"]
            or model["Options"].get("com.docker.network.bridge.name") != self.bridge
            or model["Options"].get("com.docker.network.bridge.host_binding_ipv4") != "127.0.0.1"
            or model["Options"].get("com.docker.network.bridge.enable_ip_masquerade") != "false"
            or model["Options"].get("com.docker.network.bridge.gateway_mode_ipv4") != "nat"
        ):
            raise RuntimeError("Disposable network ownership or isolation mismatch")

    def close(self):
        if self.id is None:
            return
        if not self.removed:
            self.verify()
            # Remove the network before rules: if any container remains attached,
            # retain rules/state rather than exposing running services.
            self.docker("network", "rm", self.id)
            self.removed = True
            self.save_state()
        else:
            # A previous rule deletion may have failed after Docker removed the
            # network. Never remove its orphan rules if the interface was reused.
            bridge = self.run(["ip", "link", "show", "dev", self.bridge], check=False)
            if bridge.returncode != 1:
                raise RuntimeError(
                    "Cannot clean orphan rules while their bridge exists or is unknown"
                )
        for chain, rule in self.rules():
            found = self.firewall("-C", chain, *rule, check=False)
            if found.returncode == 0:
                self.firewall("-D", chain, *rule)
            elif found.returncode != 1:
                raise RuntimeError("Cannot inspect disposable firewall rule during cleanup")
        self.id = None
        self.state.unlink()

    @classmethod
    def from_state(cls, path: Path):
        if sys.platform != "linux" or path.is_symlink():
            raise RuntimeError("Only a local Linux network ownership record is permitted")
        record = json.loads(path.read_text())
        network = cls(record["name"], path)
        if (
            record.get("version") != 1
            or not re.fullmatch(r"[a-f0-9]{64}", record["id"])
            or not re.fullmatch(r"[a-f0-9]{16}", record["token"])
            or record["bridge"] != "ttci" + record["token"][:10]
            or not isinstance(record.get("removed", False), bool)
        ):
            raise RuntimeError("Invalid disposable network ownership record")
        network.id, network.token, network.bridge = record["id"], record["token"], record["bridge"]
        network.removed = record.get("removed", False)
        return network


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("create", "cleanup"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--name")
    args = parser.parse_args()
    if args.operation == "create":
        if not args.name:
            parser.error("create requires --name")
        PublishedNetwork(args.name, args.state).create()
    elif args.state.exists():
        PublishedNetwork.from_state(args.state).close()


if __name__ == "__main__":
    main()
