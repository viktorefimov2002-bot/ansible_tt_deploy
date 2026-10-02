"""Revision, durable job and role contracts against migrated PostgreSQL."""

import asyncio
import json
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from apps.api.main import create_app
from apps.execution.jobs import execution_handlers
from apps.execution.ports import ExecutionResult, Outcome
from apps.jobs.service import CancellationUnsafe, JobService, LostClaim
from apps.jobs.worker import Worker
from apps.persistence.database import transaction
from apps.persistence.models import (
    AuditEvent,
    Device,
    DeviceCredential,
    Server,
    ServerConfigRevision,
    VpnUser,
)
from apps.servers.configuration import ServerConfiguration, validated_config_result
from apps.servers.schemas import CreateConfigRevision, CreateServer
from apps.servers.service import ServerError, ServerService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport, due, expire
from tests.test_migrations import database, migrate  # noqa: F401
from tests.test_servers import payload


@pytest.mark.parametrize(
    "config",
    [
        {"ipv6_available": "true"},
        {"ipv6_available": 1},
        {"allow_private_network_connections": "false"},
        {"tls_handshake_timeout_secs": 0},
        {"tls_handshake_timeout_secs": 121},
        {"tls_handshake_timeout_secs": True},
        {"tls_handshake_timeout_secs": "10"},
        {"client_listener_timeout_secs": 86401},
        {"connection_establishment_timeout_secs": 601},
        {"tcp_connections_timeout_secs": 2592001},
        {"udp_connections_timeout_secs": 86401},
        {"raw_config": "secret"},
        {"command": "id"},
        {"playbook": "site.yml"},
        {"listen_address": "0.0.0.0:443"},
        {"password": "secret"},
        {"credentials_file": "/tmp/users.toml"},
    ],
)
def test_closed_configuration_validation(config):
    with pytest.raises(ValidationError):
        CreateConfigRevision(idempotency_key="valid", config=config)


@pytest.mark.parametrize(
    "changes",
    [
        {"revision_id": str(uuid4())},
        {"state": "active"},
        {"active": False},
        {"active": "yes"},
        {"secret": "fixture-secret"},
    ],
)
def test_configuration_report_requires_matching_revision_and_health(changes):
    revision_id = str(uuid4())
    with pytest.raises(ValueError):
        validated_config_result(
            {"revision_id": revision_id},
            {"revision_id": revision_id, "state": "applied", "active": True} | changes,
        )


class Port:
    def __init__(self):
        self.calls = []
        self.state = "applied"
        self.outcome = Outcome.SUCCEEDED
        self.report = True
        self.malformed = False

    async def execute(self, request, emit):
        self.calls.append(request)
        report = (
            {
                "revision_id": request.config_apply.revision_id,
                "state": self.state,
                "active": self.state != "rollback_failed",
            }
            if self.report
            else None
        )
        if self.malformed:
            report["secret"] = "fixture-secret"
        return ExecutionResult(self.outcome, config_apply=report)


async def setup(auth, key):  # noqa: F811
    actor, _, _ = await account(auth)
    servers = ServerService(auth.engine, key)
    server = await servers.create(CreateServer.model_validate(payload()), actor, "create")
    sid = server["id"]
    async with transaction(auth.engine) as db:
        node = await db.get(Server, sid)
        node.trusttunnel_version = "1.2.3"
        node.lifecycle_state = "installed"
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    port = Port()
    worker = Worker(jobs, execution_handlers(port, servers))
    return actor, servers, sid, jobs, port, worker


async def revision(servers, sid, actor, config=None, idempotency_key=None):
    return await servers.create_revision(
        sid,
        CreateConfigRevision(idempotency_key=idempotency_key or uuid4().hex, config=config or {}),
        actor,
        "create-revision",
    )


async def apply(servers, sid, actor, rid, idempotency_key=None):
    return await servers.enqueue(
        sid,
        "server.config.apply",
        idempotency_key or uuid4().hex,
        actor,
        "apply-revision",
        {"revision_id": str(rid)},
    )


async def test_revision_creation_immutable_history_and_concurrent_numbering(auth, key):  # noqa: F811
    actor, servers, sid, _, _, _ = await setup(auth, key)
    first, replay = await asyncio.gather(
        revision(servers, sid, actor, idempotency_key="create"),
        revision(servers, sid, actor, idempotency_key="create"),
    )
    assert first["id"] == replay["id"] and first["revision"] == 1
    assert first["status"] == "validated" and first["config"] == ServerConfiguration().model_dump()
    with pytest.raises(ServerError) as conflict:
        await revision(servers, sid, actor, {"ipv6_available": False}, "create")
    assert conflict.value.status == 409
    rows = await asyncio.gather(*(revision(servers, sid, actor) for _ in range(4)))
    assert sorted(row["revision"] for row in rows) == [2, 3, 4, 5]
    assert [row["revision"] for row in await servers.list_revisions(sid, 1, 2)] == [4, 3]
    for statement in (
        "UPDATE server_config_revisions SET config_json='{}'::jsonb",
        "UPDATE server_config_revisions SET revision=revision+10",
        "UPDATE server_config_revisions SET created_by=NULL",
        "UPDATE server_config_revisions SET created_at=created_at+interval '1 second'",
        "UPDATE server_config_revisions SET idempotency_key=NULL",
        "DELETE FROM server_config_revisions",
        "TRUNCATE server_config_revisions CASCADE",
    ):
        with pytest.raises(IntegrityError):
            async with auth.engine.begin() as db:
                await db.execute(text(statement))
    assert len(await servers.list_revisions(sid)) == 5


async def test_apply_is_durable_idempotent_and_preserves_user_and_credentials(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    async with transaction(auth.engine) as db:
        user = VpnUser(display_name="Existing", device_limit=5)
        db.add(user)
        await db.flush()
        device = Device(user_id=user.id, name="Existing phone")
        db.add(device)
        await db.flush()
        credential = DeviceCredential(
            device_id=device.id,
            server_id=sid,
            username="existing-user",
            status="active",
            secret_ciphertext=b"test-only-secret-ciphertext",
            config_ciphertext=b"test-only-config-ciphertext",
        )
        db.add(credential)
        await db.flush()
        cid, uid = credential.id, user.id
        before_ssh = (await db.get(Server, sid)).ssh_private_ciphertext
    rev = await revision(servers, sid, actor, {"udp_connections_timeout_secs": 123})
    jobs.queue.available = False
    first, replay = await asyncio.gather(
        apply(servers, sid, actor, rev["id"], "apply"),
        apply(servers, sid, actor, rev["id"], "apply"),
    )
    assert first.id == replay.id
    assert first.parameters == {"revision_id": str(rev["id"])}
    assert first.replay_safe and not first.cancellable
    assert (await servers.get(sid))["config_revision_id"] is None
    assert (await servers.get_revision(sid, rev["id"]))["status"] == "apply_pending"
    await asyncio.gather(worker.execute(first.id), worker.execute(first.id))
    await worker.execute(first.id)
    assert len(port.calls) == 1
    assert port.calls[0].config_apply.config.udp_connections_timeout_secs == 123
    assert port.calls[0].config_apply.previous_config == ServerConfiguration()
    saved = await jobs.get(first.id)
    assert saved.status == "succeeded"
    assert (await servers.get(sid))["config_revision_id"] == rev["id"]
    row = await servers.get_revision(sid, rev["id"])
    assert row["current"] and row["applied_at"] and row["failure_code"] is None
    with pytest.raises(ServerError) as duplicate:
        await apply(servers, sid, actor, rev["id"], "another-apply")
    assert duplicate.value.status == 409
    with pytest.raises(LostClaim):
        await jobs.execution_result(first.id, uuid4(), ExecutionResult(Outcome.SUCCEEDED))
    async with transaction(auth.engine) as db:
        values = (
            await db.execute(
                select(
                    DeviceCredential.status,
                    DeviceCredential.secret_ciphertext,
                    DeviceCredential.config_ciphertext,
                ).where(DeviceCredential.id == cid)
            )
        ).one()
        assert values == ("active", b"test-only-secret-ciphertext", b"test-only-config-ciphertext")
        assert (await db.get(VpnUser, uid)).device_limit == 5
        assert (await db.get(Server, sid)).ssh_private_ciphertext == before_ssh
        audit = (await db.scalars(select(AuditEvent))).all()
        assert any(a.action == "server.config.apply.succeeded" for a in audit)
        assert "PRIVATE KEY" not in json.dumps([a.details for a in audit]) + str(saved.result)


@pytest.mark.parametrize("state", ["rolled_back", "rollback_failed"])
async def test_failed_apply_preserves_previous_revision_and_never_retries(auth, key, state):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    old = await revision(servers, sid, actor)
    baseline = await apply(servers, sid, actor, old["id"])
    await worker.execute(baseline.id)
    current = await revision(servers, sid, actor, {"ipv6_available": False})
    job = await apply(servers, sid, actor, current["id"], "failed-apply")
    port.state = state
    await worker.execute(job.id)
    await worker.execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.attempts == 1
    assert saved.error_code == "config_" + state
    assert saved.result["config_apply"]["state"] == state
    server = await servers.get(sid)
    assert server["config_revision_id"] == old["id"] and server["config_state"] == state
    assert server["status"] == ("reachable" if state == "rolled_back" else "unknown")
    row = await servers.get_revision(sid, current["id"])
    assert not row["current"] and row["status"] == state
    assert row["failure_code"] == "config_" + state and row["applied_at"] is None
    assert (await servers.get_revision(sid, old["id"]))["current"]
    assert len(port.calls) == 2
    assert (await apply(servers, sid, actor, current["id"], "failed-apply")).id == job.id
    next_rev = await revision(servers, sid, actor)
    recovery = await apply(servers, sid, actor, next_rev["id"])
    request = await servers.resolve(sid, "server.config.apply", recovery.parameters)
    assert request.config_apply.previous_config == ServerConfiguration.model_validate(old["config"])
    await jobs.cancel(recovery.id, actor, "cancel")
    assert (await servers.get(sid))["config_state"] == state


async def test_transport_failure_and_lost_worker_replay_same_immutable_intent(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    rev = await revision(servers, sid, actor)
    job = await apply(servers, sid, actor, rev["id"])
    port.report = False
    port.outcome = Outcome.UNREACHABLE
    await worker.execute(job.id)
    assert (await jobs.get(job.id)).status == "queued"
    assert (await servers.get(sid))["config_state"] == "apply_pending"
    await due(jobs, job.id)
    await jobs.claim(job.id)
    await expire(jobs, job.id)
    await jobs.recover()
    assert (await jobs.get(job.id)).status == "queued"
    await due(jobs, job.id)
    port.report, port.outcome = True, Outcome.SUCCEEDED
    await worker.execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == "succeeded" and saved.attempts == 3
    assert len(set(c.config_apply.revision_id for c in port.calls)) == 1
    assert (await servers.get(sid))["config_revision_id"] == rev["id"]


async def test_durable_rollback_receipt_survives_crash_before_terminal_completion(auth, key):  # noqa: F811
    actor, servers, sid, jobs, _, _ = await setup(auth, key)
    rev = await revision(servers, sid, actor)
    job = await apply(servers, sid, actor, rev["id"])
    claimed = await jobs.claim(job.id)
    await jobs.execution_result(
        job.id,
        claimed.claim_token,
        ExecutionResult(
            Outcome.SUCCEEDED,
            config_apply={"revision_id": str(rev["id"]), "state": "rolled_back", "active": True},
        ),
    )
    await expire(jobs, job.id)
    await jobs.recover()
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.error_code == "config_rolled_back"
    assert (await servers.get_revision(sid, rev["id"]))["status"] == "rolled_back"


async def test_exhausted_transport_failures_leave_unconfirmed_revision_unknown(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    baseline = await revision(servers, sid, actor)
    old_job = await apply(servers, sid, actor, baseline["id"])
    await worker.execute(old_job.id)
    rev = await revision(servers, sid, actor)
    job = await apply(servers, sid, actor, rev["id"])
    port.report, port.outcome = False, Outcome.UNREACHABLE
    for _ in range(3):
        await due(jobs, job.id)
        await worker.execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.attempts == 3
    assert (await servers.get(sid))["config_revision_id"] == baseline["id"]
    assert (await servers.get(sid))["config_state"] == "unknown"
    assert (await servers.get_revision(sid, rev["id"]))["status"] == "unknown"


async def test_queued_cancellation_concurrent_admission_and_unknown_outcome(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    one = await revision(servers, sid, actor)
    two = await revision(servers, sid, actor)
    job = await apply(servers, sid, actor, one["id"], "shared")
    with pytest.raises(ServerError) as conflict:
        await apply(servers, sid, actor, two["id"], "shared")
    assert conflict.value.status == 409
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.restart", "blocked", actor, "restart")
    await jobs.cancel(job.id, actor, "cancel")
    assert (await servers.get(sid))["config_state"] == "idle"
    assert (await servers.get_revision(sid, one["id"]))["failure_code"] == "cancelled"
    job = await apply(servers, sid, actor, two["id"], "uncertain")
    claimed = await jobs.claim(job.id)
    with pytest.raises(CancellationUnsafe):
        await jobs.cancel(job.id, actor, "cancel")
    await jobs.complete(job.id, claimed.claim_token, code="unsafe_outcome")
    assert (await servers.get(sid))["config_revision_id"] is None
    assert (await servers.get_revision(sid, two["id"]))["status"] == "unknown"
    third = await revision(servers, sid, actor)
    assert (await apply(servers, sid, actor, two["id"], "uncertain")).id == job.id
    recovery = await apply(servers, sid, actor, third["id"])
    request = await servers.resolve(sid, "server.config.apply", recovery.parameters)
    assert request.config_apply.previous_config == ServerConfiguration()
    await jobs.cancel(recovery.id, actor, "cancel-recovery")
    assert (await servers.get(sid))["config_state"] == "unknown"


async def test_malformed_apply_receipt_is_secret_safe_and_never_confirms_activation(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    third = await revision(servers, sid, actor)
    invalid = await apply(servers, sid, actor, third["id"])
    port.malformed = True
    await worker.execute(invalid.id)
    saved = await jobs.get(invalid.id)
    assert saved.status == "failed" and saved.error_code == "execution_invalid"
    assert "fixture-secret" not in str(saved.result) + str(saved.history)
    assert (await servers.get(sid))["config_revision_id"] is None


async def test_configuration_and_credential_admission_share_server_lock(auth, key):  # noqa: F811
    from apps.persistence.models import Job
    from apps.vpn.schemas import DeviceInput, UserInput
    from apps.vpn.service import VpnError, VpnService

    actor, servers, sid, jobs, _, _ = await setup(auth, key)
    vpn = VpnService(auth.engine, key)
    user = await vpn.create_user(
        UserInput(display_name="Preserve", access_mode="all"), actor, "user"
    )
    device = await vpn.create_device(user["id"], DeviceInput(name="Phone"), actor, "device")
    rev = await revision(servers, sid, actor)

    async def issue():
        try:
            return await vpn.create_credential(user["id"], device["id"], sid, actor, "credential")
        except VpnError as exc:
            return exc

    async def submit():
        try:
            return await apply(servers, sid, actor, rev["id"])
        except ServerError as exc:
            return exc

    results = await asyncio.gather(submit(), issue())
    assert sum(isinstance(item, Job) for item in results) == 1
    assert sum(isinstance(item, ServerError | VpnError) for item in results) == 1
    admitted = next(item for item in results if isinstance(item, Job))
    await jobs.cancel(admitted.id, actor, "cancel")
    next_rev = await revision(servers, sid, actor)
    config_job = await apply(servers, sid, actor, next_rev["id"])
    with pytest.raises(VpnError) as denied:
        await vpn.create_credential(user["id"], device["id"], sid, actor, "blocked-credential")
    assert denied.value.status == 409
    await jobs.cancel(config_job.id, actor, "cancel")
    assert (
        await vpn.create_credential(user["id"], device["id"], sid, actor, "allowed")
    ).type == "credential.create"


async def test_revision_api_rbac_ownership_validation_and_read_state(auth, key):  # noqa: F811
    actor, servers, sid, jobs, _, _ = await setup(auth, key)
    app = create_app()
    app.state.auth, app.state.servers, app.state.jobs = auth, servers, jobs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, creds, _ = await account(auth)
        admin = await login(client, creds)
        _, creds, _ = await account(auth, "viewer")
        viewer = await login(client, creds)
        path = f"/api/servers/{sid}/config-revisions"
        body = {"idempotency_key": "create", "config": {}}
        assert (await client.get(path)).status_code == 401
        assert (await client.post(path, json=body)).status_code == 401
        assert (await client.post(path, headers=viewer, json=body)).status_code == 403
        bad = await client.post(
            path, headers=admin, json=body | {"config": {"password": "fixture-secret"}}
        )
        assert bad.status_code == 422 and "fixture-secret" not in bad.text
        response = await client.post(path, headers=admin, json=body)
        assert response.status_code == 201, response.text
        rid = response.json()["id"]
        assert (await client.get(path, headers=viewer)).json()[0]["id"] == rid
        assert (await client.get(path + "/" + rid, headers=viewer)).status_code == 200
        apply_path = path + "/" + rid + "/apply"
        apply_body = {"idempotency_key": "apply"}
        assert (await client.post(apply_path, json=apply_body)).status_code == 401
        assert (await client.post(apply_path, headers=viewer, json=apply_body)).status_code == 403
        assert (
            await client.post(apply_path, headers=admin, json=apply_body | {"command": "id"})
        ).status_code == 422
        missing = f"/api/servers/{uuid4()}/config-revisions"
        assert (await client.get(missing, headers=viewer)).status_code == 404
        assert (await client.post(missing, headers=admin, json=body)).status_code == 404
        assert (await client.get(path + "/" + str(uuid4()), headers=viewer)).status_code == 404
        other = await servers.create(
            CreateServer.model_validate(payload() | {"name": "node-2"}), actor, "other"
        )
        foreign = f"/api/servers/{other['id']}/config-revisions/{rid}"
        assert (await client.get(foreign, headers=admin)).status_code == 404
        async with transaction(auth.engine) as db:
            node = await db.get(Server, other["id"])
            node.trusttunnel_version = "1.2.3"
        assert (
            await client.post(foreign + "/apply", headers=admin, json=apply_body)
        ).status_code == 404
        accepted = await client.post(apply_path, headers=admin, json=apply_body)
        assert accepted.status_code == 202, accepted.text
        assert (await client.get(path + "/" + rid, headers=viewer)).json()[
            "status"
        ] == "apply_pending"


async def test_invalid_legacy_configuration_is_redacted_and_never_applied(auth, key):  # noqa: F811
    actor, servers, sid, _, _, _ = await setup(auth, key)
    async with transaction(auth.engine) as db:
        legacy = ServerConfigRevision(
            server_id=sid,
            revision=1,
            config_json={"password": "fixture-secret"},
        )
        db.add(legacy)
        await db.flush()
        rid = legacy.id
        (await db.get(Server, sid)).config_revision_id = rid
    legacy = await servers.get_revision(sid, rid)
    assert legacy["config"] is None and not legacy["validation_valid"]
    assert "fixture-secret" not in repr(await servers.list_revisions(sid))
    next_rev = await revision(servers, sid, actor)
    with pytest.raises(ServerError) as invalid:
        await apply(servers, sid, actor, next_rev["id"])
    assert invalid.value.status == 422 and "fixture-secret" not in invalid.value.message


async def test_revision_migration_preserves_existing_history_and_enables_guards(database):  # noqa: F811
    await migrate(database, "0010_server_lifecycle", downgrade=True)
    async with database.begin() as db:
        sid = await db.scalar(
            text(
                "INSERT INTO servers(name,hostname,ssh_user) "
                "VALUES ('legacy','legacy.test','manager') RETURNING id"
            )
        )
        rid = await db.scalar(
            text(
                "INSERT INTO server_config_revisions(server_id,revision,config_json) "
                "VALUES (:s,1,'{}'::jsonb) RETURNING id"
            ),
            {"s": sid},
        )
        await db.execute(
            text("UPDATE servers SET config_revision_id=:r WHERE id=:s"), {"r": rid, "s": sid}
        )
    await migrate(database)
    legacy = await ServerService(database, None).get_revision(sid, rid)
    assert legacy["validation_valid"] and legacy["config"] == ServerConfiguration().model_dump()
    async with database.connect() as db:
        assert (
            await db.scalar(text("SELECT config_revision_id FROM servers WHERE id=:s"), {"s": sid})
            == rid
        )
    with pytest.raises(IntegrityError):
        async with database.begin() as db:
            await db.execute(text("DELETE FROM server_config_revisions WHERE id=:r"), {"r": rid})
