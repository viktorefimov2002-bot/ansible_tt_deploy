"""Mandatory Linux network drill: all allowed and denied targets are disposable and local."""

import json
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.ci.networking import PublishedNetwork, published_port, wait_tcp  # noqa: E402

REDIS_IMAGE = "redis:7.4.11-alpine3.21"


@contextmanager
def owned_directory():
    tools = (ROOT / ".tools").resolve()
    tools.mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="ci-network-", dir=tools)).resolve()
    try:
        yield work
    finally:
        # Keep ownership records if Docker refuses cleanup, so rules remain recoverable.
        if not any((work / name).exists() for name in ("protected.json", "outside.json")):
            if work.parent != tools or not work.name.startswith("ci-network-"):
                raise RuntimeError("Refusing cleanup outside the owned network drill directory")
            shutil.rmtree(work)


class HostCanary:
    """Listen only on this drill's private bridge address, counting any unexpected access."""

    def __init__(self, address):
        self.socket = socket.socket()
        self.socket.bind((address, 0))
        self.socket.listen()
        self.socket.settimeout(0.1)
        self.port = self.socket.getsockname()[1]
        self.accepted = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.socket.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                self.accepted += 1

    def close(self):
        self.stop.set()
        self.socket.close()
        self.thread.join(timeout=2)


def dropped_packets(network, chain):
    result = network.firewall("-L", chain, "-n", "-v", "-x")
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 7 and fields[2] == "DROP" and fields[5] == network.bridge:
            return int(fields[0])
    raise RuntimeError("Cannot find the exact disposable bridge deny rule")


def redis_connection(network, container, host, port, *, allowed):
    result = network.docker(
        "exec",
        container,
        "sh",
        "-c",
        'REDISCLI_AUTH="$REDIS_PASSWORD" timeout 2 redis-cli -h "$1" -p "$2" PING',
        "probe",
        host,
        str(port),
        check=False,
    )
    success = result.returncode == 0 and result.stdout.strip() == "PONG"
    if success != allowed:
        raise RuntimeError("Disposable network allowed/denied protocol check failed")


def start_redis(network, name, env_file, *, publish=False):
    options = [
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network.name,
        "--dns",
        "127.0.0.1",
        "--env-file",
        str(env_file),
        "--tmpfs",
        "/run/redis:rw,size=1m",
        "--tmpfs",
        "/data:rw,size=16m",
        "-v",
        f"{ROOT / 'infra/compose/redis'}:/usr/local/etc/redis:ro",
    ]
    if publish:
        options.extend(["-p", "127.0.0.1::6379"])
    network.docker(
        *options,
        "--entrypoint",
        "/bin/sh",
        REDIS_IMAGE,
        "/usr/local/etc/redis/start.sh",
    )
    for _ in range(100):
        result = network.docker(
            "exec",
            name,
            "sh",
            "-c",
            'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli PING',
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip() == "PONG":
            return
        time.sleep(0.1)
    raise RuntimeError("Disposable Redis canary did not become ready")


def main():
    if sys.platform != "linux":
        raise SystemExit("This mandatory network drill requires Linux, Docker 28+ and iptables")
    token = secrets.token_hex(8)
    with owned_directory() as work:
        protected = PublishedNetwork("ttcp-ci-smoke-" + token, work / "protected.json")
        outside = PublishedNetwork("ttcp-ci-canary-" + token, work / "outside.json")
        server, peer, canary = (
            "ttcp-ci-smoke-server-" + token,
            "ttcp-ci-smoke-peer-" + token,
            "ttcp-ci-smoke-canary-" + token,
        )
        env_file = work / "redis.env"
        password = secrets.token_hex(32)
        env_file.write_text(f"REDIS_PASSWORD={password}\n")
        env_file.chmod(0o600)
        host_canary = None
        try:
            protected.create()
            outside.create()
            start_redis(protected, server, env_file, publish=True)
            start_redis(protected, peer, env_file)
            start_redis(outside, canary, env_file, publish=True)
            port = published_port(protected.docker("port", server, "6379/tcp").stdout)
            wait_tcp(port, "network drill Redis", timeout=5)
            # A real Redis request must work across publication, including the reply.
            with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                connection.sendall(f"AUTH {password}\r\nPING\r\n".encode())
                reply = connection.makefile("rb")
                try:
                    if reply.readline() != b"+OK\r\n" or reply.readline() != b"+PONG\r\n":
                        raise RuntimeError("Loopback publication failed its Redis protocol check")
                finally:
                    reply.close()
            redis_connection(protected, peer, server, 6379, allowed=True)
            network_info = json.loads(
                protected.docker("network", "inspect", protected.name).stdout
            )[0]
            gateway = network_info["IPAM"]["Config"][0]["Gateway"]
            host_canary = HostCanary(gateway)
            before = dropped_packets(protected, "INPUT")
            redis_connection(protected, peer, gateway, host_canary.port, allowed=False)
            if dropped_packets(protected, "INPUT") <= before or host_canary.accepted:
                raise RuntimeError("Disposable bridge failed to deny new host connections")
            canary_info = json.loads(outside.docker("inspect", canary).stdout)[0]
            canary_ip = canary_info["NetworkSettings"]["Networks"][outside.name]["IPAddress"]
            # The second network belongs to this drill; no Internet/production packet is sent.
            # Docker may reject direct cross-bridge traffic in raw PREROUTING,
            # before DOCKER-USER. Verify our exact forwarding deny rule is installed
            # and independently require the disposable cross-bridge request to fail.
            forwarding = next(rule for chain, rule in protected.rules() if chain == "DOCKER-USER")
            protected.firewall("-C", "DOCKER-USER", *forwarding)
            redis_connection(protected, peer, canary_ip, 6379, allowed=False)
            for container, network in ((server, protected), (peer, protected), (canary, outside)):
                model = json.loads(network.docker("inspect", container).stdout)[0]
                if model["HostConfig"]["Dns"] != ["127.0.0.1"]:
                    raise RuntimeError("Disposable containers must not forward external DNS")
        finally:
            if host_canary:
                host_canary.close()
            # Exact names allocated above, followed by ownership-verified network/rule removal.
            protected.docker("rm", "-f", "-v", server, peer, canary, check=False)
            for network in (outside, protected):
                if network.id:
                    network.close()
                if network.state.exists():
                    raise RuntimeError(
                        "Disposable network ownership record survived successful cleanup"
                    )
                if network.docker("network", "inspect", network.name, check=False).returncode == 0:
                    raise RuntimeError("Disposable network survived successful cleanup")
                for chain, rule in network.rules():
                    if network.firewall("-C", chain, *rule, check=False).returncode != 1:
                        raise RuntimeError("Disposable deny rule survived successful cleanup")
            for container in (server, peer, canary):
                if protected.docker("inspect", container, check=False).returncode == 0:
                    raise RuntimeError("Disposable Redis container survived successful cleanup")
        print(
            "PASS: loopback Redis protocol, same-bridge DNS/peers, denied host/cross-bridge egress"
        )


if __name__ == "__main__":
    main()
