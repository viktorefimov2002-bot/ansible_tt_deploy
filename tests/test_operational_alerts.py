"""Durable transitions over migrated PostgreSQL and bounded private signals."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from apps.api.main import create_app
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.monitoring.alerts import (
    RULES,
    OperationalAlertService,
    advance,
    observation,
    values_by_server,
)
from apps.monitoring.query import ALERT_QUERY, MetricsClient, MetricsUnavailable
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, OperationalAlert, Server
from apps.visibility.service import VisibilityService
from apps.worker.__main__ import run
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport
from tests.test_migrations import ROOT, database  # noqa: F401

NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)


def at(seconds):
    return NOW + timedelta(seconds=seconds)


def cursor(kind="NODE_OFFLINE"):
    return OperationalAlert(server_id=uuid4(), type=kind)


def step(state, seconds, bad=True, *, observed=None):
    return advance(
        state,
        bad,
        at(seconds).timestamp() if observed is None else observed,
        at(seconds),
        RULES[state.type],
    )


@pytest.mark.parametrize("kind", RULES)
def test_threshold_duration_and_hysteresis_recovery(kind):
    state = cursor(kind)
    duration = RULES[kind].open_seconds
    for second in range(0, duration, 30):
        assert step(state, second) is None
    transition, incident = step(state, duration)
    assert transition == "open" and incident == state.incident_id
    assert step(state, duration + 30) is None
    for second in (duration + 60, duration + 90):
        assert step(state, second, False) is None
    assert step(state, duration + 120, False) == ("recovered", incident)
    assert state.incident_id is None and state.opened_at is None
    assert step(state, duration + 150, False) is None


def test_flapping_duplicate_scrapes_unknown_gaps_and_out_of_order_samples():
    state = cursor()
    assert step(state, 0) is None
    assert step(state, 30, False) is None  # Transient failure.
    assert step(state, 60) is None
    assert step(state, 120, observed=at(60).timestamp()) is None  # Same scrape.
    assert state.incident_id is None
    assert step(state, 121, None) is None
    assert step(state, 150) is None
    assert step(state, 180, False) is None
    assert step(state, 210) is None
    assert step(state, 240) is None
    assert step(state, 239, False) is None  # Older evaluator cannot reset evidence.
    assert step(state, 270)[0] == "open"
    incident = state.incident_id
    assert step(state, 300, False) is None
    assert step(state, 330) is None  # Recovery flaps.
    assert step(state, 360, False) is None
    assert step(state, 600, False) is None  # No recovery through a worker outage.
    assert step(state, 630, False) is None
    assert step(state, 660, False) == ("recovered", incident)
    assert step(state, 690) is None
    assert step(state, 720) is None
    assert step(state, 750)[1] != incident  # A new incident after genuine recovery.


@pytest.mark.parametrize("kind,low", [("DISK_HIGH", 85), ("MEMORY_HIGH", 80)])
def test_resource_hysteresis_and_invalid_samples(kind, low):
    timestamp = NOW.timestamp()
    values = dict(observed=timestamp, scrape=1)
    field = "disk_percent" if kind == "DISK_HIGH" else "memory_percent"
    for value, expected in [(90, True), (low, False), (89, None), (-1, None), (101, None)]:
        values[field] = value
        assert observation(kind, values, timestamp, timestamp) == (
            expected,
            timestamp if 0 <= value <= 100 else None,
        )
    for value in (None, float("nan"), float("inf")):
        values[field] = value
        assert observation(kind, values, timestamp, timestamp)[0] is None
    values["scrape"] = 0
    assert observation(kind, values, timestamp, timestamp) == (None, None)


def test_ambiguous_metrics_are_unknown_and_no_source_timestamp_is_invented():
    rows = samples(uuid4(), 0)
    duplicate = rows[0] | {"value": [NOW.timestamp(), "0"]}
    assert values_by_server([*rows, duplicate])[(None, "collector")] is None
    assert observation("NODE_OFFLINE", {}, NOW.timestamp(), NOW.timestamp()) == (None, None)


def test_node_recovery_clock_requires_distinct_node_samples():
    observed = NOW.timestamp()
    values = dict(observed=observed, scrape=1)
    for second in (0, 30, 60, 90):
        collector_time = at(second).timestamp()
        assert observation("NODE_OFFLINE", values, collector_time, collector_time) == (
            False,
            observed,
        )
    # Staleness remains evidence while collector scrapes keep succeeding.
    assert observation("NODE_OFFLINE", values, at(120).timestamp(), at(120).timestamp()) == (
        True,
        at(120).timestamp(),
    )


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"probe": 0}, True),
        ({"process": 0}, True),
        ({"active": 0}, True),
        ({"probe": None}, None),
        ({"process": None}, None),
        ({"active": 2}, None),
        ({}, False),
    ],
)
def test_service_requires_trusted_explicit_health_evidence(changes, expected):
    values = dict(observed=NOW.timestamp(), scrape=1, probe=1, active=1, process=1) | changes
    assert observation("SERVICE_DOWN", values, NOW.timestamp(), NOW.timestamp())[0] == expected


def samples(server_id, second, **changes):
    timestamp = at(second).timestamp()
    values = (
        dict(
            scrape=1,
            observed=timestamp,
            probe=1,
            active=1,
            process=1,
            memory_percent=40,
            disk_percent=50,
        )
        | changes
    )
    return [
        {"metric": {"metric": name}, "value": [timestamp, str(value)]}
        for name, value in {"collector": 1, "collector_observed": timestamp}.items()
    ] + [
        {
            "metric": {"server_id": str(server_id), "metric": name},
            "value": [timestamp, str(value)],
        }
        for name, value in values.items()
    ]


async def managed(engine):
    async with transaction(engine) as db:
        node = Server(
            name="managed",
            hostname="node.test",
            ssh_user="ttcp",
            ssh_host_key="test-pin",
            ssh_private_ciphertext=b"test-encrypted-placeholder",
            lifecycle_state="installed",
        )
        db.add(node)
        await db.flush()
        return node.id


async def records(engine):
    async with transaction(engine) as db:
        return list(
            await db.scalars(
                select(AuditEvent)
                .where(AuditEvent.action.like("alert.%"))
                .order_by(AuditEvent.created_at)
            )
        )


async def poll(engine, server_id, second, **changes):
    # New service instance on every call proves correctness is not process-local.
    metrics = SimpleNamespace(alerts=AsyncMock(return_value=samples(server_id, second, **changes)))
    await OperationalAlertService(engine, metrics).reconcile(now=at(second))


async def test_concurrent_evaluators_restart_duplicate_and_recovery(database):  # noqa: F811
    node = await managed(database)
    for second in (0, 30):
        await poll(database, node, second, scrape=0)
    await asyncio.gather(*(poll(database, node, 60, scrape=0) for _ in range(4)))
    events = await records(database)
    assert len(events) == 1 and events[0].result == "open"
    identity = events[0].request_id
    for second in (90, 120):
        await poll(database, node, second, scrape=0)
    for second in (150, 180, 210, 240):
        await poll(database, node, second)
    events = await records(database)
    assert [event.result for event in events] == ["open", "recovered"]
    assert {event.request_id for event in events} == {identity}
    assert all(event.target_id == node and event.actor_type == "system" for event in events)
    assert all(event.details == {} for event in events)
    async with transaction(database) as db:
        assert await db.scalar(select(func.count()).select_from(OperationalAlert)) == 4


async def test_repeated_healthy_node_sample_cannot_recover_incident(database):  # noqa: F811
    node = await managed(database)
    for second in (0, 30, 60):
        await poll(database, node, second, scrape=0)
    for second in (90, 120, 150, 180):
        # The collector clock advances, but no second successful node scrape exists.
        await poll(database, node, second, observed=at(90).timestamp())
    assert [event.result for event in await records(database)] == ["open"]
    for second in (210, 240, 270):
        await poll(database, node, second)
    events = await records(database)
    assert [event.result for event in events] == ["open", "recovered"]
    assert events[0].request_id == events[1].request_id


@pytest.mark.parametrize("kind", ["DISK_HIGH", "MEMORY_HIGH", "SERVICE_DOWN"])
async def test_all_conditions_persist_one_active_incident_and_recover(database, kind):  # noqa: F811
    node = await managed(database)
    changes = {
        "DISK_HIGH": {"disk_percent": 95},
        "MEMORY_HIGH": {"memory_percent": 95},
        "SERVICE_DOWN": {"active": 0, "process": 0},
    }[kind]
    duration = RULES[kind].open_seconds
    for second in range(0, duration + 61, 30):
        await poll(database, node, second, **changes)
    assert [event.action for event in await records(database)] == ["alert." + kind.lower()]
    for second in range(duration + 90, duration + 181, 30):
        await poll(database, node, second)
    assert [event.result for event in await records(database)] == ["open", "recovered"]


async def test_metrics_collector_and_node_outages_preserve_active_incidents(database):  # noqa: F811
    node = await managed(database)
    for second in (0, 30, 60):
        await poll(database, node, second, active=0)
    metrics = SimpleNamespace(alerts=AsyncMock(side_effect=MetricsUnavailable("safe")))
    service = OperationalAlertService(database, metrics)
    await service.reconcile(now=at(90))
    rows = samples(node, 120)
    rows[0]["value"][1] = "0"  # Collector outage, despite old healthy node values.
    metrics.alerts = AsyncMock(return_value=rows)
    await service.reconcile(now=at(120))
    rows = samples(node, 150)
    rows[1]["value"][1] = str(at(30).timestamp())  # Collector stale.
    metrics.alerts = AsyncMock(return_value=rows)
    await service.reconcile(now=at(150))
    assert len(await records(database)) == 1
    for second in (180, 210, 240):
        await poll(database, node, second, scrape=0)  # Node incident, service unknown.
    events = await records(database)
    assert [event.result for event in events] == ["open", "open"]
    for second in (270, 300, 330):
        await poll(database, node, second)
    assert [event.result for event in await records(database)] == [
        "open",
        "open",
        "recovered",
        "recovered",
    ]


async def test_outage_breaks_pending_threshold_and_stale_node_opens(database):  # noqa: F811
    node = await managed(database)
    for second in range(0, 271, 30):
        await poll(database, node, second, disk_percent=95)
    metrics = SimpleNamespace(alerts=AsyncMock(side_effect=MetricsUnavailable("safe")))
    await OperationalAlertService(database, metrics).reconcile(now=at(300))
    await poll(database, node, 330, disk_percent=95)
    assert not await records(database)
    for second in (360, 390, 420):
        await poll(database, node, second, observed=at(0).timestamp())
    assert [event.action for event in await records(database)] == ["alert.node_offline"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("enabled", False),
        ("ssh_user", "legacy"),
        ("ssh_host_key", None),
        ("ssh_private_ciphertext", None),
    ],
)
async def test_disabled_or_unmanaged_nodes_do_not_alert(database, field, value):  # noqa: F811
    node = await managed(database)
    async with transaction(database) as db:
        await db.execute(update(Server).where(Server.id == node).values({field: value}))
    for second in (0, 30, 60, 90):
        await poll(database, node, second, scrape=0)
    assert not await records(database)


@pytest.mark.parametrize(
    "field,value",
    [
        ("lifecycle_state", "uninstalled"),
        ("lifecycle_state", "deploy_pending"),
        ("lifecycle_state", "update_pending"),
        ("lifecycle_state", "restart_pending"),
        ("lifecycle_state", "uninstall_pending"),
        ("config_state", "apply_pending"),
    ],
)
async def test_planned_service_operations_do_not_duplicate_failure_notifications(
    database,  # noqa: F811
    field,
    value,
):
    node = await managed(database)
    async with transaction(database) as db:
        await db.execute(update(Server).where(Server.id == node).values({field: value}))
    for second in (0, 30, 60, 90):
        await poll(database, node, second, active=0)
    assert not await records(database)
    async with transaction(database) as db:
        await db.execute(
            update(Server)
            .where(Server.id == node)
            .values(lifecycle_state="installed", config_state="idle")
        )
    for second in (120, 150, 180):
        await poll(database, node, second, active=0)
    assert len(await records(database)) == 1


async def test_transactional_event_failure_does_not_lose_or_duplicate_transition(database):  # noqa: F811
    node = await managed(database)
    for second in (0, 30):
        await poll(database, node, second, scrape=0)
    async with database.begin() as db:
        await db.execute(
            text(
                "CREATE FUNCTION reject_alert() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'test storage failure'; END $$"
            )
        )
        await db.execute(
            text(
                "CREATE TRIGGER reject_alert BEFORE INSERT ON audit_events "
                "FOR EACH ROW EXECUTE FUNCTION reject_alert()"
            )
        )
    with pytest.raises(DBAPIError):
        await poll(database, node, 60, scrape=0)
    async with transaction(database) as db:
        state = await db.get(OperationalAlert, (node, "NODE_OFFLINE"))
        assert state.incident_id is None and state.last_evaluated_at == at(30)
        await db.execute(text("DROP TRIGGER reject_alert ON audit_events"))
        await db.execute(text("DROP FUNCTION reject_alert()"))
    await poll(database, node, 60, scrape=0)
    await poll(database, node, 90, scrape=0)
    assert len(await records(database)) == 1


async def test_worker_maintenance_evaluates_during_redis_loss_and_replay(database):  # noqa: F811
    node = await managed(database)
    transport = MemoryTransport()
    transport.available = False
    jobs = JobService(database, transport, transport)
    stop, second = asyncio.Event(), 0

    async def maintenance():
        nonlocal second
        await poll(database, node, second, scrape=0)
        second += 30
        if second == 90:
            stop.set()

    await asyncio.wait_for(Worker(jobs, maintenance=maintenance).run(stop), 5)
    transport.available = True
    transport.queue.clear()  # Redis restart/loss of ephemeral entries.
    await poll(database, node, 90, scrape=0)
    assert len(await records(database)) == 1


async def test_actual_alerts_visible_to_admin_viewer_reads_do_not_evaluate_and_rbac(auth):  # noqa: F811
    node = await managed(auth.engine)
    for second in (0, 30, 60):
        await poll(auth.engine, node, second, scrape=0)
    event = (await records(auth.engine))[0]
    app = create_app()
    app.state.auth = auth
    app.state.visibility = VisibilityService(auth.engine)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, body, _ = await account(auth, "viewer")
        viewer = await login(client, body)
        _, body, _ = await account(auth)
        admin = await login(client, body)
        assert (await client.get("/api/notifications")).status_code == 401
        for headers in (admin, viewer, viewer):
            response = await client.get("/api/notifications", headers=headers)
            assert response.status_code == 200
            alerts = [item for item in response.json()["items"] if item["target_id"] == str(node)]
            assert len(alerts) == 1 and alerts[0]["title"] == "Node monitoring unavailable"
            assert "test-pin" not in response.text and "test-encrypted" not in response.text
            audit = await client.get("/api/audit?action=alert.node_offline", headers=headers)
            assert audit.json()["total"] == 1
        path = f"/api/notifications/{event.id}/read"
        assert (await client.put(path, headers=viewer, json={"read": True})).status_code == 403
        for _ in range(2):
            assert (await client.put(path, headers=admin, json={"read": True})).status_code == 200
        page = (await client.get("/api/notifications", headers=viewer)).json()
        assert (
            next(item for item in page["items"] if item["id"] == str(event.id))["read_at"] is None
        )
        assert (await client.post("/api/monitoring/alerts", headers=admin)).status_code == 404
    async with transaction(auth.engine) as db:
        state = await db.get(OperationalAlert, (node, "NODE_OFFLINE"))
        assert state.last_evaluated_at == at(60)
    assert len(await records(auth.engine)) == 1


async def test_alert_constraints_and_runtime_grants(database):  # noqa: F811
    node = await managed(database)
    for state in (
        OperationalAlert(server_id=node, type="NETWORK_SATURATION"),
        OperationalAlert(server_id=node, type="NODE_OFFLINE", incident_id=uuid4()),
        OperationalAlert(server_id=node, type="NODE_OFFLINE", pending_state=True),
    ):
        with pytest.raises(IntegrityError):
            async with transaction(database) as db:
                db.add(state)
    role = "ttcp_alert_test_" + uuid4().hex
    async with database.begin() as db:
        schema = await db.scalar(text("SELECT current_schema()"))
        await db.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
    try:
        grants = (ROOT / "infra/compose/postgres/runtime-grants.sql").read_text()
        grants = grants.replace(':"app_role"', f'"{role}"').replace("public.", f'"{schema}".')
        grants = grants.replace("SCHEMA public", f'SCHEMA "{schema}"')
        async with database.begin() as db:
            for statement in grants.split(";"):
                if statement.strip():
                    await db.execute(text(statement))
            await db.execute(text(f'SET LOCAL ROLE "{role}"'))
            await db.execute(
                text("INSERT INTO operational_alerts(server_id,type) VALUES (:id,'NODE_OFFLINE')"),
                {"id": node},
            )
            await db.execute(text("UPDATE operational_alerts SET last_evaluated_at=now()"))
            assert await db.scalar(text("SELECT count(*) FROM operational_alerts")) == 1
    finally:
        async with database.begin() as db:
            await db.execute(text(f'DROP OWNED BY "{role}"'))
            await db.execute(text(f'DROP ROLE "{role}"'))


async def test_alert_client_uses_only_fixed_instant_query_and_redacts_outages():
    requests = []

    def reply(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"resultType": "vector", "result": samples(uuid4(), 0)},
            },
        )

    client = MetricsClient(transport=httpx.MockTransport(reply))
    try:
        assert await client.alerts(NOW.timestamp())
        assert requests[0].url.path == "/api/v1/query"
        assert requests[0].url.params["query"] == ALERT_QUERY
        assert "timestamp(node_memory_MemAvailable_bytes)" in ALERT_QUERY
        assert "group_left timestamp(ttcp_node_scrape_success)" in ALERT_QUERY
        await client.close()
        client.client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(503, text="private-address-password")
            ),
            base_url="http://test",
        )
        with pytest.raises(MetricsUnavailable, match="^Metrics unavailable$"):
            await client.alerts(NOW.timestamp())
    finally:
        await client.close()


async def test_tick_throttles_and_recovers_after_database_failure(caplog):
    service = OperationalAlertService(None, None)
    service.reconcile = AsyncMock(side_effect=DBAPIError("query", {}, Exception("secret")))
    await service.tick()
    await service.tick()
    assert service.reconcile.await_count == 1 and "secret" not in caplog.text
    service.next_poll = 0
    service.reconcile.side_effect = None
    await service.tick()
    assert service.reconcile.await_count == 2


async def test_runtime_maintenance_wiring_and_metrics_cleanup(settings, monkeypatch):
    dependencies = AsyncMock()
    metrics = SimpleNamespace(close=AsyncMock())
    alerts = SimpleNamespace(tick=AsyncMock())
    vpn = SimpleNamespace(expire_due=AsyncMock())
    stop = asyncio.Event()

    @asynccontextmanager
    async def connected(_):
        yield dependencies

    async def consume(worker, stop):
        await worker.maintenance()
        stop.set()

    monkeypatch.setattr("apps.worker.__main__.connected_dependencies", connected)
    monkeypatch.setattr("apps.worker.__main__.MetricsClient", lambda *_: metrics)
    monkeypatch.setattr("apps.worker.__main__.OperationalAlertService", lambda *_: alerts)
    monkeypatch.setattr("apps.worker.__main__.VpnService", lambda *_: vpn)
    monkeypatch.setattr("apps.worker.__main__.Worker.run", consume)
    await asyncio.wait_for(run(settings, stop), 2)
    alerts.tick.assert_awaited_once()
    vpn.expire_due.assert_awaited_once()
    metrics.close.assert_awaited_once()
