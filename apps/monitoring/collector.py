"""Bounded SSH collection from the loopback exporter installed by TTCP-010."""

import asyncio
import os
import re
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from apps.persistence.database import transaction
from apps.persistence.models import Server

METRIC = re.compile(
    r"^(node_[a-zA-Z0-9_:]+)(\{[^\r\n]*\})? "
    r"([+-]?(?:(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|Inf)|NaN)"
    r"(?: [0-9]+)?$"
)
ALLOWED = (
    "node_cpu_seconds_total",
    "node_memory_MemTotal_bytes",
    "node_memory_MemAvailable_bytes",
    "node_load1",
    "node_load5",
    "node_load15",
    "node_filesystem_size_bytes",
    "node_filesystem_avail_bytes",
    "node_disk_read_bytes_total",
    "node_disk_written_bytes_total",
    "node_boot_time_seconds",
    "node_network_receive_bytes_total",
    "node_network_transmit_bytes_total",
    "node_network_receive_packets_total",
    "node_network_transmit_packets_total",
    "node_network_receive_errs_total",
    "node_network_transmit_errs_total",
    "node_network_receive_drop_total",
    "node_network_transmit_drop_total",
    "node_network_speed_bytes",
)
REMOTE = (
    "python3 -c 'import urllib.request; "
    "opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
    'print(opener.open("http://127.0.0.1:9100/metrics", '
    'timeout=5).read().decode(), end="")\''
)


def label(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def filter_metrics(raw: bytes, server_id: str) -> list[str]:
    if len(raw) > 2_000_000:
        raise ValueError("exporter response too large")
    lines = []
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("node_"):
            continue
        match = METRIC.fullmatch(line)
        if not match:
            raise ValueError("invalid exporter sample")
        name, existing, value = match.groups()
        if name not in ALLOWED:
            continue
        if existing and re.search(r"(^|[,{}])\s*(server_id|instance|job)\s*=", existing):
            raise ValueError("reserved exporter label")
        labels = (
            existing[:-1] + f',server_id="{server_id}"' + "}"
            if existing
            else f'{{server_id="{server_id}"}}'
        )
        lines.append(f"{name}{labels} {value}")
        if len(lines) > 10000:
            raise ValueError("too many exporter samples")
    if not lines:
        raise ValueError("no selected exporter samples")
    return lines


async def scrape_node(server, cipher, *, runner=None):
    if not server.ssh_host_key or not server.ssh_private_ciphertext or server.ssh_user != "ttcp":
        return []
    try:
        key = cipher.decrypt(server.ssh_private_ciphertext).decode("ascii")
    except (InvalidToken, UnicodeError):
        return []
    host = server.hostname
    port = server.ssh_port
    with tempfile.TemporaryDirectory(prefix="ttcp-metrics-") as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        identity = root / "identity"
        identity.write_text(key, encoding="ascii")
        identity.chmod(0o600)
        known_hosts = root / "known_hosts"
        pin_name = host if port == 22 else f"[{host}]:{port}"
        known_hosts.write_text(f"{pin_name} {server.ssh_host_key}\n", encoding="ascii")
        known_hosts.chmod(0o600)
        argv = [
            "ssh",
            "-F",
            "/dev/null",
            "-T",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts}",
            "-o",
            "GlobalKnownHostsFile=/dev/null",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "BatchMode=yes",
            "-o",
            "ControlMaster=no",
            "-o",
            "ConnectTimeout=5",
            "-i",
            str(identity),
            "-p",
            str(port),
            f"ttcp@{host}",
            REMOTE,
        ]
        if runner is None:
            runner = asyncio.create_subprocess_exec
        process = await runner(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout = await asyncio.wait_for(process.stdout.read(2_000_001), timeout=12)
            if len(stdout) > 2_000_000:
                process.kill()
                await process.wait()
                return []
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()
            return []
        if process.returncode != 0:
            return []
        try:
            return filter_metrics(stdout, str(server.id))
        except (ValueError, UnicodeError):
            return []


async def collect(engine, encryption_key, *, scrape=scrape_node):
    cipher = Fernet(encryption_key.encode("ascii"))
    async with transaction(engine) as db:
        servers = list((await db.scalars(select(Server).order_by(Server.id))).all())
    lines = ["# TYPE ttcp_server_info gauge", "# TYPE ttcp_node_scrape_success gauge"]
    enabled = [server for server in servers if server.enabled]
    results = await asyncio.gather(
        *(scrape(server, cipher) for server in enabled), return_exceptions=True
    )
    samples = dict(zip((server.id for server in enabled), results, strict=True))
    for server in servers:
        server_id = str(server.id)
        lines.append(
            f'ttcp_server_info{{server_id="{server_id}",name="{label(server.name)}",'
            f'status="{label(server.status)}",enabled="{str(server.enabled).lower()}"}} 1'
        )
        result = samples.get(server.id, [])
        successful = isinstance(result, list) and bool(result)
        lines.append(f'ttcp_node_scrape_success{{server_id="{server_id}"}} {int(successful)}')
        if successful:
            lines.extend(result)
    return "\n".join(lines) + "\n"
