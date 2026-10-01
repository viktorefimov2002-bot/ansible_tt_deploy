"""Visibility API permissions, redaction and lifecycle over migrated PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from apps.api.main import create_app
from apps.jobs.service import JobService, LostClaim
from apps.jobs.worker import Handler, RetryableError, Worker
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, NotificationRead
from apps.visibility.service import NOTIFICATION_TYPES, VisibilityService, notification_view
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport, create, due, expire
from tests.test_migrations import ROOT, database  # noqa: F401


async def event(engine, **overrides):
    values = dict(
        actor_type="system",
        action="user.expire",
        target_type="vpn_user",
        target_id=uuid4(),
        request_id="visibility-test",
        result="success",
        details={"raw_error": "password=never-expose", "token": "never-expose-token"},
    )
    values.update(overrides)
    async with transaction(engine) as db:
        record = AuditEvent(**values)
        db.add(record)
        await db.flush()
        return record


def test_notification_templates_do_not_use_metadata_or_untrusted_text():
    for action, result, target in NOTIFICATION_TYPES:
        record = AuditEvent(
            id=uuid4(),
            created_at=datetime.now(UTC),
            action=action,
            result=result,
            target_type=target,
            target_id=uuid4(),
            request_id="safe-request",
            details={"message": "private-key-password-token"},
        )
        view = notification_view(record, None)
        assert view["severity"] in ("info", "warning", "critical")
        assert "private-key-password-token" not in str(view)
        assert "details" not in view and "metadata" not in view


async def test_audit_filters_stable_pagination_and_redaction(database):  # noqa: F811
    visibility = VisibilityService(database)
    at = datetime.now(UTC) - timedelta(minutes=5)
    actor, target = uuid4(), uuid4()
    ids = [UUID(int=value) for value in (1, 2, 3)]
    for value in ids:
        await event(
            database,
            id=value,
            actor_type="admin",
            actor_id=actor,
            target_id=target,
            created_at=at,
        )
    await event(database, action="user.update", created_at=at - timedelta(days=1))
    first = await visibility.audit(offset=0, limit=2)
    second = await visibility.audit(offset=2, limit=2)
    assert first["total"] == second["total"] == 4
    assert [item["id"] for item in first["items"]] == ids[1:][::-1]
    assert second["items"][0]["id"] == ids[0]
    assert not ({"metadata", "details"} & first["items"][0].keys())
    assert "never-expose" not in str(first)
    for field, value in {
        "action": "user.expire",
        "actor_type": "admin",
        "actor_id": actor,
        "target_type": "vpn_user",
        "target_id": target,
        "result": "success",
        "request_id": "visibility-test",
    }.items():
        page = await visibility.audit(**{field: value})
        assert page["items"] and all(row[field] == value for row in page["items"])
    page = await visibility.audit(since=at, until=at)
    assert page["total"] == 3
    assert (await visibility.audit(action="nonexistent"))["items"] == []
    assert (await visibility.audit(offset=100))["items"] == []


async def test_api_read_permissions_input_validation_and_errors(auth):  # noqa: F811
    app = create_app()
    app.state.auth, app.state.visibility = auth, VisibilityService(auth.engine)
    record = await event(auth.engine)
    ordinary = await event(auth.engine, action="user.update")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, body, _ = await account(auth, "viewer")
        viewer = await login(client, body)
        _, body, _ = await account(auth)
        admin = await login(client, body)
        for path in ("/api/audit", "/api/notifications"):
            assert (await client.get(path)).status_code == 401
            response = await client.get(path, headers=viewer)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["x-request-id"]
            for query in ("?offset=-1", "?offset=100001", "?limit=0", "?limit=101"):
                assert (await client.get(path + query, headers=admin)).status_code == 422
        for query in (
            "?actor_type=root",
            "?actor_id=bad",
            "?target_id=bad",
            "?action=" + "x" * 129,
            "?target_type=" + "x" * 65,
            "?result=" + "x" * 33,
            "?request_id=" + "x" * 129,
            "?action=password%3Dnever-expose",
            "?since=2026-01-02",
            "?since=2026-01-02T00:00:00",
            "?since=2026-02-02T00:00:00Z&until=2026-01-02T00:00:00Z",
        ):
            response = await client.get("/api/audit" + query, headers=viewer)
            assert response.status_code == 422, query
            assert "never-expose" not in response.text
        path = f"/api/notifications/{record.id}/read"
        assert (await client.put(path, json={"read": True})).status_code == 401
        assert (await client.put(path, headers=viewer, json={"read": True})).status_code == 403
        for body in ({"read": "true"}, {"read": 1}, {}, {"read": True, "admin_id": str(uuid4())}):
            assert (await client.put(path, headers=admin, json=body)).status_code == 422
        for missing in (uuid4(), ordinary.id):
            assert (
                await client.put(
                    f"/api/notifications/{missing}/read", headers=admin, json={"read": True}
                )
            ).status_code == 404
        assert (
            await client.put("/api/notifications/not-uuid/read", headers=admin, json={"read": True})
        ).status_code == 422
        read = await client.put(path, headers=admin, json={"read": True})
        assert read.status_code == 200 and read.json()["read_at"]
        assert (await client.put(path, headers=admin, json={"read": True})).json() == read.json()
        assert (await client.put(path, headers=admin, json={"read": False})).json()[
            "read_at"
        ] is None
        response = await client.get("/api/audit?action=user.expire", headers=admin)
        assert response.json()["total"] == 1
        assert "never-expose" not in response.text
        response = await client.get("/api/notifications", headers=viewer)
        assert any(row["id"] == str(record.id) for row in response.json()["items"])
        assert not any(row["id"] == str(ordinary.id) for row in response.json()["items"])
        assert "never-expose" not in response.text
        app.state.visibility = None
        assert (await client.get("/api/audit", headers=viewer)).status_code == 503
        assert (await client.get("/api/notifications", headers=viewer)).status_code == 503


async def test_notifications_read_lifecycle_is_durable_idempotent_and_per_admin(auth):  # noqa: F811
    first_admin, _, _ = await account(auth)
    second_admin, _, _ = await account(auth)
    visibility = VisibilityService(auth.engine)
    one = await event(auth.engine)
    two = await event(auth.engine, action="job.failed", result="failure", target_type="job")
    await event(auth.engine, action="user.update")
    page = await visibility.notifications(first_admin, limit=1)
    assert page["total"] == page["unread_count"] == 2
    assert page["items"][0]["id"] == two.id
    assert (await visibility.notifications(first_admin, offset=1, limit=1))["items"][0][
        "id"
    ] == one.id
    async with transaction(auth.engine) as db:
        # Listing creates no lifecycle records.
        assert await db.scalar(select(func.count()).select_from(NotificationRead)) == 0
        count = await db.scalar(select(func.count()).select_from(AuditEvent))
    reads = await asyncio.gather(
        *(visibility.set_read(two.id, first_admin, True) for _ in range(3))
    )
    assert reads[0]["read_at"] and all(row["read_at"] == reads[0]["read_at"] for row in reads)
    # A new service instance sees committed state; Redis is not involved.
    visibility = VisibilityService(auth.engine)
    unread = await visibility.notifications(first_admin, unread_only=True)
    assert unread["total"] == unread["unread_count"] == 1
    assert [row["id"] for row in unread["items"]] == [one.id]
    assert (await visibility.notifications(second_admin))["unread_count"] == 2
    await visibility.set_read(two.id, second_admin, True)
    assert (await visibility.set_read(two.id, first_admin, False))["read_at"] is None
    assert (await visibility.set_read(two.id, first_admin, False))["read_at"] is None
    assert (await visibility.notifications(first_admin))["unread_count"] == 2
    assert (await visibility.notifications(second_admin))["unread_count"] == 1
    async with transaction(auth.engine) as db:
        assert await db.scalar(select(func.count()).select_from(NotificationRead)) == 1
        assert await db.scalar(select(func.count()).select_from(AuditEvent)) == count


async def test_terminal_job_notifications_skip_retries_and_duplicate_execution(database):  # noqa: F811
    transport = MemoryTransport()
    jobs = JobService(database, transport, transport)
    visibility = VisibilityService(database)

    async def retry(context):
        raise RetryableError("password=never-expose")

    failure = await create(jobs, max_attempts=2)
    worker = Worker(jobs, {"internal.noop": Handler(retry, True, True)})
    await worker.execute(failure.id)
    assert (await visibility.audit(action="job.failed"))["total"] == 0
    await due(jobs, failure.id)
    await worker.execute(failure.id)
    await worker.execute(failure.id)
    assert (await visibility.audit(action="job.failed"))["total"] == 1
    success = await create(jobs)
    await asyncio.gather(Worker(jobs).execute(success.id), Worker(jobs).execute(success.id))
    assert (await visibility.audit(action="job.succeeded"))["total"] == 1
    assert "never-expose" not in str(await visibility.audit())
    unsafe = await create(jobs, replay_safe=False, cancellable=False)
    claim = await jobs.claim(unsafe.id)
    await expire(jobs, unsafe.id)
    await jobs.recover()
    await jobs.recover()
    with pytest.raises(LostClaim):
        await jobs.complete(unsafe.id, claim.claim_token)
    assert (await visibility.audit(action="job.failed"))["total"] == 2
    transport.available = False
    assert (await visibility.notifications(uuid4()))["total"] == 3


async def test_read_state_foreign_keys_and_runtime_grants(database):  # noqa: F811
    from sqlalchemy.exc import DBAPIError

    role = "ttcp_visibility_" + uuid4().hex
    async with database.begin() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
        await connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
    try:
        grants = (ROOT / "infra/compose/postgres/runtime-grants.sql").read_text()
        grants = grants.replace(':"app_role"', f'"{role}"')
        grants = grants.replace("public.", f'"{schema}".').replace(
            "SCHEMA public", f'SCHEMA "{schema}"'
        )
        async with database.begin() as connection:
            for statement in grants.split(";"):
                if statement.strip():
                    await connection.execute(text(statement))
            await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
            admin_id = await connection.scalar(
                text("INSERT INTO admins(username) VALUES ('reader') RETURNING id")
            )
            event_id = await connection.scalar(
                text(
                    "INSERT INTO audit_events(actor_type,action,target_type,result) "
                    "VALUES ('system','user.expire','vpn_user','success') RETURNING id"
                )
            )
            await connection.execute(
                text("INSERT INTO notification_reads(admin_id,event_id) VALUES (:admin,:event)"),
                {"admin": admin_id, "event": event_id},
            )
            assert await connection.scalar(text("SELECT count(*) FROM notification_reads")) == 1
            await connection.execute(text("DELETE FROM notification_reads"))
        with pytest.raises(DBAPIError):
            async with database.begin() as connection:
                await connection.execute(text(f'SET LOCAL ROLE "{role}"'))
                await connection.execute(
                    text(
                        "INSERT INTO notification_reads(admin_id,event_id) VALUES (:admin,:event)"
                    ),
                    {"admin": admin_id, "event": uuid4()},
                )
    finally:
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{role}"'))
            await connection.execute(text(f'DROP ROLE "{role}"'))
