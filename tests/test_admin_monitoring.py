"""Monitoring authentication, fixed query bounds, freshness and secret-free summaries."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from apps.api.auth_service import Principal
from apps.api.main import create_app
from apps.monitoring.query import (
    CURRENT_QUERY,
    MAX_BYTES,
    RECENT_QUERY,
    MetricsClient,
    MetricsUnavailable,
)
from apps.monitoring.service import MonitoringService
from apps.persistence.database import transaction
from apps.persistence.models import Server
from tests.test_auth import account, auth, key  # noqa: F401
from tests.test_migrations import database  # noqa: F401

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def sample(metric_key, value, server_id=None):
    labels = {"metric": metric_key, "instance": "internal-secret-label"}
    if server_id is not None:
        labels["server_id"] = str(server_id)
    return {"metric": labels, "value": [NOW.timestamp(), str(value)]}


def node(**changes):
    return SimpleNamespace(
        **dict(id=uuid4(), name="Node", enabled=True, status="reachable", last_seen_at=None)
        | changes
    )


def current(server, **changes):
    values = (
        dict(
            scrape=1,
            observed=NOW.timestamp() - 30,
            probe=1,
            active=1,
            process=1,
            restarts=2,
            cpu_percent=12.5,
            memory_percent=40,
            disk_percent=55,
            load1=0.3,
            network_receive_bytes_per_second=300,
            network_transmit_bytes_per_second=200,
        )
        | changes
    )
    return [sample(metric_key, value, server.id) for metric_key, value in values.items()] + [
        sample("collector", 1),
        sample("collector_observed", NOW.timestamp() - 30),
    ]


def service(servers, samples, recent=None, *, checks=None):
    dependencies = SimpleNamespace(
        check=AsyncMock(return_value=checks or {"postgres": True, "redis": True})
    )
    metrics = SimpleNamespace(snapshot=AsyncMock(return_value=(samples, recent or [])))
    subject = MonitoringService(None, dependencies, metrics)
    subject.inventory = AsyncMock(return_value=servers)
    return subject


async def test_summary_keeps_management_and_service_health_separate_and_redacts_labels():
    server = node(status="unknown")
    recent = [
        {
            "metric": {"server_id": str(server.id), "metric": "cpu_percent"},
            "values": [[NOW.timestamp() - 60, "8"], [NOW.timestamp(), "NaN"]],
        }
    ]
    result = await service([server], current(server), recent).summary("1h", now=NOW)
    row = result["servers"][0]
    assert row["health"] == "healthy" and row["management_status"] == "unknown"
    assert row["metrics"]["cpu_percent"] == 12.5
    assert row["service"] == {
        "probe": "healthy",
        "active": True,
        "process_running": True,
        "automatic_restarts": 2,
    }
    assert len(row["recent"]) == 61
    assert row["recent"][-2]["cpu_percent"] == 8
    assert row["recent"][-1]["cpu_percent"] is None
    assert row["recent"][0]["memory_percent"] is None
    assert row["recent"][0]["cpu_percent"] is None
    assert result["system"]["status"] == "healthy"
    assert "internal-secret-label" not in json.dumps(result)


@pytest.mark.parametrize(
    "changes,health,probe",
    [
        ({"observed": NOW.timestamp() - 91}, "unavailable", "unknown"),
        ({"observed": NOW.timestamp() + 10}, "unavailable", "unknown"),
        ({"scrape": 0}, "unavailable", "unknown"),
        ({"probe": 0}, "degraded", "unavailable"),
        ({"active": 0}, "degraded", "healthy"),
        ({"process": 0}, "degraded", "healthy"),
    ],
)
async def test_stale_failed_and_inactive_service_states(changes, health, probe):
    server = node()
    result = await service([server], current(server, **changes)).summary("6h", now=NOW)
    row = result["servers"][0]
    assert row["health"] == health and row["service"]["probe"] == probe
    assert result["system"]["status"] == "degraded"
    if health == "unavailable":
        assert all(value is None for value in row["metrics"].values())
        assert row["service"]["active"] is None
    if probe != "healthy":
        assert row["service"]["automatic_restarts"] is None


async def test_disabled_unknown_collector_failure_and_upstream_outage():
    disabled = node(enabled=False)
    server = node()
    subject = service([disabled, server], [])
    result = await subject.summary("24h", now=NOW)
    assert [row["health"] for row in result["servers"]] == ["disabled", "unknown"]
    assert result["system"]["collector"] == "unknown"
    samples = current(server)
    samples[-2] = sample("collector", 0)
    subject = service([server], samples)
    assert (await subject.summary("1h", now=NOW))["servers"][0]["health"] == "unavailable"
    subject.metrics.snapshot.side_effect = MetricsUnavailable("safe error")
    result = await subject.summary("1h", now=NOW)
    assert result["system"]["victoriametrics"] == "unavailable"
    assert result["servers"][0]["health"] == "unknown"
    subject.metrics.snapshot.side_effect = None
    subject.metrics.snapshot.return_value = (current(server), [])
    subject.dependencies.check.return_value = {"postgres": True, "redis": False}
    result = await subject.summary("1h", now=NOW)
    assert result["system"]["redis"] == "unavailable" and result["system"]["status"] == "degraded"
    assert result["servers"][0]["health"] == "healthy"


async def test_fixed_queries_bounded_windows_and_upstream_protocol():
    requests = []

    def handle(request):
        requests.append(request)
        matrix = request.url.path.endswith("query_range")
        assert request.url.host == "victoriametrics"
        assert request.url.params["query"] == (RECENT_QUERY if matrix else CURRENT_QUERY)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "matrix" if matrix else "vector", "result": []},
            },
        )

    client = MetricsClient(transport=httpx.MockTransport(handle))
    try:
        for window, duration, step in (("1h", 3600, 60), ("6h", 21600, 300), ("24h", 86400, 600)):
            assert await client.snapshot(window, NOW.timestamp()) == ([], [])
            query = requests[-1].url.params
            assert float(query["end"]) - float(query["start"]) == duration
            assert int(query["step"]) == step
            assert duration // step + 1 <= 145
        assert client.client.trust_env is False
    finally:
        await client.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="password=do-not-expose"),
        httpx.Response(302, headers={"Location": "http://evil.test"}),
        httpx.Response(200, text="not JSON"),
        httpx.Response(200, json={"status": "error", "error": "private error"}),
        httpx.Response(200, content=b"x" * (MAX_BYTES + 1)),
        httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {}, "value": ["invalid", "1"]}],
                },
            },
        ),
    ],
)
async def test_upstream_failures_are_bounded_and_redacted(response):
    client = MetricsClient(transport=httpx.MockTransport(lambda _: response))
    try:
        with pytest.raises(MetricsUnavailable, match="^Metrics unavailable$"):
            await client.snapshot("1h", NOW.timestamp())
    finally:
        await client.close()


async def test_absolute_timeout_bounds_slow_metrics_dependency():
    async def handle(request):
        await asyncio.Event().wait()

    client = MetricsClient(timeout=0.05, transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(MetricsUnavailable):
            await asyncio.wait_for(client.snapshot("1h", NOW.timestamp()), timeout=0.3)
    finally:
        await client.close()


async def test_monitoring_authentication_window_validation_and_no_store():
    app = create_app()
    server = node()
    app.state.monitoring = service([server], current(server))
    fake_auth = SimpleNamespace(authenticate=AsyncMock(return_value=None), denied=AsyncMock())
    app.state.auth = fake_auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://admin.test") as http:
        response = await http.get("/api/monitoring")
        assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
        assert "Max-Age=0" in response.headers["set-cookie"]
        for role in ("admin", "viewer"):
            fake_auth.authenticate.return_value = Principal(uuid4(), "Test", role, uuid4())
            headers = {"Authorization": "Bearer " + "A" * 43}
            response = await http.get("/api/monitoring?window=6h", headers=headers)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert response.json()["window"] == "6h"
            response = await http.get("/api/monitoring?window=1000h", headers=headers)
            assert response.status_code == 422 and response.json() == {"detail": "Invalid request"}
            assert (await http.post("/api/monitoring", headers=headers)).status_code == 405


def test_metrics_target_is_operator_configuration_not_a_url(settings):
    for host in ("http://evil.test", "host/path", "host:123", "host@evil.test", ""):
        with pytest.raises(ValidationError):
            type(settings)(**(settings.model_dump() | {"victoriametrics_host": host}))


async def test_monitoring_inventory_uses_durable_names_without_credentials(database):  # noqa: F811
    async with transaction(database) as db:
        server = Server(
            name="Database node",
            hostname="private.example",
            ssh_user="ttcp",
            ssh_private_ciphertext=b"secret ciphertext",
        )
        db.add(server)
        await db.flush()
        server_id = server.id
    subject = MonitoringService(
        database,
        SimpleNamespace(check=AsyncMock(return_value={"postgres": True, "redis": True})),
        SimpleNamespace(snapshot=AsyncMock(return_value=([], []))),
    )
    result = await subject.summary("1h", now=NOW)
    assert result["servers"][0]["name"] == "Database node"
    assert result["servers"][0]["id"] == str(server_id)
    assert "private.example" not in json.dumps(result)
    assert "secret ciphertext" not in json.dumps(result)


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_cookie_monitoring_requires_same_origin_and_current_account(auth, role):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    app.state.monitoring = MonitoringService(
        auth.engine,
        SimpleNamespace(check=AsyncMock(return_value={"postgres": True, "redis": True})),
        SimpleNamespace(snapshot=AsyncMock(return_value=([], []))),
    )
    _, credentials, _ = await account(auth, role)
    headers = {"X-TTCP-Admin": "web"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://admin.test") as http:
        response = await http.post(
            "/api/auth/login", json=credentials | {"session_mode": "cookie"}, headers=headers
        )
        assert response.status_code == 200
        response = await http.get("/api/monitoring", headers=headers)
        assert response.status_code == 200 and response.json()["servers"] == []
        assert response.headers["cache-control"] == "no-store"
        assert (await http.get("/api/monitoring")).status_code == 403
        assert (
            await http.get("/api/monitoring", headers=headers | {"Origin": "https://vpn.test"})
        ).status_code == 403
        assert (await http.post("/api/auth/logout", headers=headers)).status_code == 204
        response = await http.get("/api/monitoring", headers=headers)
        assert response.status_code == 401 and "Max-Age=0" in response.headers["set-cookie"]
