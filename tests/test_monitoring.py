from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet

from apps.monitoring import collector
from apps.monitoring.collector import filter_metrics, scrape_node, service_metrics
from apps.persistence.database import transaction
from apps.persistence.models import Server
from tests.test_migrations import database  # noqa: F401


def server(cipher, **changes):
    values = dict(
        id=uuid4(),
        name="edge-1",
        status="reachable",
        enabled=True,
        ssh_user="ttcp",
        hostname="node.example",
        ssh_port=2222,
        ssh_host_key="ssh-ed25519 AAAA",
        ssh_private_ciphertext=cipher.encrypt(b"secret-key"),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_exporter_samples_are_scoped_and_bounded():
    raw = b'node_cpu_seconds_total{cpu="0",mode="idle"} 12\nnode_load1 1.5e+02\nother_metric 9\n'
    assert filter_metrics(raw, "server-1") == [
        'node_cpu_seconds_total{cpu="0",mode="idle",server_id="server-1"} 12',
        'node_load1{server_id="server-1"} 1.5e+02',
    ]
    with pytest.raises(ValueError):
        filter_metrics(b'node_load1{server_id="forged"} 1\n', "server-1")
    with pytest.raises(ValueError):
        filter_metrics(b"node_load1 1\n" * 200_000, "server-1")


def test_fixed_service_report_exposes_only_bounded_metrics():
    report = (
        b"LoadState=loaded\nActiveState=active\nSubState=running\n"
        b"MainPID=123\nNRestarts=4\nVersion=1.0.41\n"
    )
    lines = service_metrics(report, "server-1")
    assert 'ttcp_service_probe_success{server_id="server-1"} 1' in lines
    assert 'ttcp_service_active{server_id="server-1"} 1' in lines
    assert 'ttcp_process_running{server_id="server-1"} 1' in lines
    assert 'ttcp_service_automatic_restarts_total{server_id="server-1"} 4' in lines
    assert 'ttcp_service_running_version_info{server_id="server-1",version="1.0.41"} 1' in lines
    assert not any("123" in line for line in lines)
    assert service_metrics(
        report.replace(b"Version=1.0.41", b"Version=unsafe-value"), "server-1"
    ) == [line for line in lines if "running_version" not in line]


def test_missing_service_and_malformed_report():
    missing = b"LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\nNRestarts=0\n"
    lines = service_metrics(missing, "server-1")
    assert 'ttcp_service_active{server_id="server-1"} 0' in lines
    assert 'ttcp_process_running{server_id="server-1"} 0' in lines
    assert not any("running_version" in line for line in lines)
    assert service_metrics(b"arbitrary output", "server-1") == [
        'ttcp_service_probe_success{server_id="server-1"} 0'
    ]
    assert service_metrics(missing + b"Secret=unexpected\n", "server-1") == [
        'ttcp_service_probe_success{server_id="server-1"} 0'
    ]
    assert service_metrics(missing + b"MainPID=123\n", "server-1") == [
        'ttcp_service_probe_success{server_id="server-1"} 0'
    ]


@pytest.mark.asyncio
async def test_pinned_ssh_scrape_uses_existing_loopback_exporter():
    cipher = Fernet(Fernet.generate_key())
    observed = []

    class Output:
        async def read(self, count):
            return b"node_load1 2\n"

    class Process:
        stdout = Output()
        returncode = 0

        async def wait(self):
            return 0

    async def runner(*argv, **kwargs):
        observed.extend(argv)
        assert "127.0.0.1:9100/metrics" in argv[-1]
        assert "StrictHostKeyChecking=yes" in argv
        assert "BatchMode=yes" in argv
        assert "ControlMaster=no" in argv
        return Process()

    node = server(cipher)
    assert await scrape_node(node, cipher, runner=runner) == [
        f'node_load1{{server_id="{node.id}"}} 2',
        f'ttcp_service_probe_success{{server_id="{node.id}"}} 0',
    ]
    assert "-L" not in observed and "-R" not in observed


@pytest.mark.asyncio
async def test_missing_credentials_do_not_start_ssh():
    cipher = Fernet(Fernet.generate_key())

    async def forbidden(*args, **kwargs):
        pytest.fail("SSH must not run")

    assert (
        await scrape_node(server(cipher, ssh_private_ciphertext=None), cipher, runner=forbidden)
        == []
    )


@pytest.mark.asyncio
async def test_node_and_service_reports_share_one_pinned_ssh_scrape():
    cipher = Fernet(Fernet.generate_key())
    node = server(cipher)
    raw = (
        b"node_load1 2\n# TTCP_STATUS_BEGIN\n"
        b"LoadState=loaded\nActiveState=active\nSubState=running\n"
        b"MainPID=77\nNRestarts=2\nVersion=1.0.41\n"
    )

    class Output:
        async def read(self, count):
            return raw

    class Process:
        stdout = Output()
        returncode = 0

        async def wait(self):
            return 0

    async def runner(*argv, **kwargs):
        assert "/usr/local/sbin/ttcp-node-status" in argv[-1]
        assert "/usr/bin/sudo -n /usr/local/sbin/ttcp-node-status" in argv[-1]
        return Process()

    lines = await scrape_node(node, cipher, runner=runner)
    assert f'node_load1{{server_id="{node.id}"}} 2' in lines
    assert f'ttcp_service_active{{server_id="{node.id}"}} 1' in lines
    assert f'ttcp_process_running{{server_id="{node.id}"}} 1' in lines
    assert f'ttcp_service_automatic_restarts_total{{server_id="{node.id}"}} 2' in lines
    assert f'ttcp_service_running_version_info{{server_id="{node.id}",version="1.0.41"}} 1' in lines


@pytest.mark.asyncio
async def test_inventory_correlates_status_and_isolates_scrape_failure(monkeypatch):
    key = Fernet.generate_key()
    cipher = Fernet(key)
    healthy = server(cipher, name="edge-a", status="reachable")
    failed = server(cipher, name="edge-b", status="unknown")

    class Database:
        async def scalars(self, statement):
            return SimpleNamespace(all=lambda: [healthy, failed])

    @asynccontextmanager
    async def fake_transaction(engine):
        yield Database()

    async def fake_scrape(node, encryption):
        if node.id == failed.id:
            raise TimeoutError
        return [f'node_load1{{server_id="{node.id}"}} 2']

    monkeypatch.setattr(collector, "transaction", fake_transaction)
    payload = await collector.collect(object(), key.decode(), scrape=fake_scrape)
    assert f'server_id="{healthy.id}",name="edge-a",status="reachable",enabled="true"' in payload
    assert f'server_id="{failed.id}",name="edge-b",status="unknown",enabled="true"' in payload
    assert f'ttcp_node_scrape_success{{server_id="{healthy.id}"}} 1' in payload
    assert f'ttcp_node_scrape_success{{server_id="{failed.id}"}} 0' in payload


@pytest.mark.asyncio
async def test_postgres_inventory_to_exposition(database):  # noqa: F811
    key = Fernet.generate_key()
    async with transaction(database) as session:
        session.add(
            Server(
                name="monitor-test",
                hostname="node.example",
                ssh_user="ttcp",
                ssh_port=22,
                status="reachable",
                enabled=True,
            )
        )

    async def fake_scrape(node, encryption):
        return [f'node_load1{{server_id="{node.id}"}} 1']

    payload = await collector.collect(database, key.decode(), scrape=fake_scrape)
    assert 'name="monitor-test",status="reachable",enabled="true"' in payload
    assert 'ttcp_node_scrape_success{server_id="' in payload
    assert 'node_load1{server_id="' in payload
