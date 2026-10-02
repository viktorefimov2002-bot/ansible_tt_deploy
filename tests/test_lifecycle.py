import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import func, select

from apps.api.main import create_app
from apps.execution.ansible import AnsibleExecutionAdapter
from apps.execution.jobs import execution_handlers
from apps.execution.ports import (
    CHECKS,
    LIFECYCLE,
    ExecutionRequest,
    ExecutionResult,
    Outcome,
    preflight_ready,
)
from apps.jobs.service import CancellationUnsafe, JobService
from apps.jobs.worker import Worker
from apps.persistence.database import transaction
from apps.persistence.models import Job, Server
from apps.servers.schemas import CreateServer, DeployWorkload, UpdateWorkload
from apps.servers.service import ServerError, ServerService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_execution import target
from tests.test_jobs import MemoryTransport
from tests.test_migrations import database  # noqa: F401
from tests.test_servers import payload


@pytest.mark.parametrize(
    "bad", ["latest", "auto", "1.2.3;id", "{{x}}", "../1.2.3", "01.2.3", "1.2.3-rc", "--help"]
)
def test_release_inputs_are_closed(bad):
    with pytest.raises(ValidationError):
        UpdateWorkload(idempotency_key="test", version=bad)
    with pytest.raises(ValidationError):
        DeployWorkload(idempotency_key="test", version="1.2.3", playbook="site.yml")


@pytest.mark.parametrize("operation", LIFECYCLE)
async def test_adapter_lifecycle_fixed_mapping_and_health_report(operation):
    parameters = {"version": "1.2.3"} if operation in ("server.deploy", "server.update") else {}
    if operation == "server.deploy":
        parameters |= {
            "domain": "vpn.example.org",
            "acme_http": True,
            "acme_email": "operator@example.org",
        }
    report = {
        "installed_version": None if operation == "server.uninstall" else "1.2.3",
        "active": operation != "server.uninstall",
    }
    paths = []

    async def runner(argv, cwd, env, output, deadline):
        paths.append(cwd)
        assert argv[-1].endswith("managed-lifecycle.yml")
        assert deadline == 600
        assert "fixture-secret" not in repr(argv) + repr(env)
        inventory = json.loads((cwd / "inventory.json").read_text())
        assert (
            "StrictHostKeyChecking=yes"
            in inventory["trusttunnel"]["hosts"]["managed"]["ansible_ssh_args"]
        )
        assert (
            json.loads((cwd / "parameters.json").read_text())["ttcp_lifecycle_payload"]["operation"]
            == operation
        )
        (cwd / "lifecycle.json").write_text(json.dumps(report))
        await output(b"password=fixture-secret\n", False)
        return 0

    events = []

    async def emit(event):
        events.append(event.value)

    request = ExecutionRequest(
        operation=operation, target=target(), lifecycle=parameters, timeout_seconds=600
    )
    adapter = AnsibleExecutionAdapter(runner=runner)
    assert (await adapter.execute(request, emit)).lifecycle == report
    assert "fixture-secret" not in repr(events)
    assert all(not path.exists() for path in paths)
    report["active"] = "yes"
    assert (await adapter.execute(request, emit)).outcome == Outcome.INVALID


def test_required_preflight_cannot_skip_os_ports_or_connectivity():
    checks = dict.fromkeys(CHECKS, "pass")
    assert preflight_ready(checks, True)
    for name in (
        "ssh",
        "os",
        "architecture",
        "disk",
        "memory",
        "time_sync",
        "tcp_443",
        "udp_443",
        "tcp_80",
    ):
        assert not preflight_ready(checks | {name: "skipped"}, True)
    assert preflight_ready(checks | {"tcp_80": "skipped"}, False)
    assert not preflight_ready(checks | {"dns_a": "skipped", "dns_aaaa": "skipped"}, True)


class Port:
    def __init__(self):
        self.calls = []
        self.outcome = Outcome.SUCCEEDED
        self.block = None

    async def execute(self, request, emit):
        self.calls.append(request)
        if self.block:
            await self.block.wait()
        if request.operation == "server.preflight":
            return ExecutionResult(self.outcome, checks=dict.fromkeys(CHECKS, "pass"))
        return ExecutionResult(
            self.outcome,
            lifecycle={
                "installed_version": None
                if request.operation == "server.uninstall"
                else request.lifecycle.version or "1.2.3",
                "active": request.operation != "server.uninstall",
            },
        )


async def setup(auth, key):  # noqa: F811
    actor, _, _ = await account(auth)
    servers = ServerService(auth.engine, key)
    server = await servers.create(CreateServer.model_validate(payload()), actor, "create")
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    port = Port()
    worker = Worker(jobs, execution_handlers(port, servers))
    return actor, servers, server["id"], jobs, port, worker


async def due(jobs, job_id):
    async with transaction(jobs.engine) as db:
        job = await db.get(Job, job_id)
        job.available_at = await db.scalar(select(func.now()))


async def ready(servers, sid, actor, worker):
    preflight = await servers.enqueue(sid, "server.preflight", uuid4().hex, actor, "preflight")
    await worker.execute(preflight.id)


async def test_durable_deploy_update_restart_uninstall_and_redeploy(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    intent = {"version": "1.2.3", "acme_email": "operator@example.org"}
    with pytest.raises(ServerError) as error:
        await servers.enqueue(sid, "server.deploy", "deploy", actor, "deploy", intent)
    assert error.value.status == 409
    await ready(servers, sid, actor, worker)
    deploy = await servers.enqueue(sid, "server.deploy", "deploy", actor, "deploy", intent)
    assert deploy.replay_safe and not deploy.cancellable
    assert (
        await servers.enqueue(sid, "server.deploy", "deploy", actor, "repeat", intent)
    ).id == deploy.id
    with pytest.raises(ServerError) as error:
        await servers.enqueue(
            sid, "server.deploy", "deploy", actor, "repeat", intent | {"version": "1.2.4"}
        )
    assert error.value.status == 409
    await worker.execute(deploy.id)
    await worker.execute(deploy.id)  # Duplicate queue delivery performs no remote mutation.
    assert len([c for c in port.calls if c.operation == "server.deploy"]) == 1
    row = await servers.get(sid)
    assert row["lifecycle_state"] == "installed" and row["trusttunnel_version"] == "1.2.3"
    for operation, parameters in (
        ("server.update", {"version": "1.2.4"}),
        ("server.restart", {}),
        ("server.uninstall", {}),
    ):
        job = await servers.enqueue(sid, operation, uuid4().hex, actor, "apply", parameters)
        if operation == "server.restart":
            assert not job.replay_safe
        await worker.execute(job.id)
        assert (await jobs.get(job.id)).status == "succeeded"
    row = await servers.get(sid)
    assert row["lifecycle_state"] == "uninstalled" and row["ssh_configured"]
    assert row["trusttunnel_version"] is None and row["desired_trusttunnel_version"] is None
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.deploy", "again", actor, "deploy", intent)
    await ready(servers, sid, actor, worker)
    deploy = await servers.enqueue(sid, "server.deploy", "again", actor, "deploy", intent)
    await worker.execute(deploy.id)
    assert (await servers.get(sid))["lifecycle_state"] == "installed"


@pytest.mark.parametrize("outcome", [Outcome.FAILED, Outcome.TIMED_OUT, Outcome.UNREACHABLE])
async def test_mutation_failure_retry_retains_intent_and_only_confirms_success(auth, key, outcome):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    await ready(servers, sid, actor, worker)
    job = await servers.enqueue(
        sid,
        "server.deploy",
        "deploy",
        actor,
        "deploy",
        {"version": "1.2.3", "acme_email": "operator@example.org"},
    )
    port.outcome = outcome
    await worker.execute(job.id)
    assert (await jobs.get(job.id)).status == "queued"
    assert (await servers.get(sid))["trusttunnel_version"] is None
    port.outcome = Outcome.SUCCEEDED
    await due(jobs, job.id)
    await worker.execute(job.id)
    assert (await jobs.get(job.id)).status == "succeeded"
    assert (await servers.get(sid))["trusttunnel_version"] == "1.2.3"


async def test_concurrent_admission_cancellation_and_restart_lost_worker(auth, key):  # noqa: F811
    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    await ready(servers, sid, actor, worker)

    async def enqueue(i):
        try:
            return await servers.enqueue(
                sid,
                "server.deploy",
                str(i),
                actor,
                "deploy",
                {"version": "1.2.3", "acme_email": "operator@example.org"},
            )
        except ServerError as exc:
            return exc

    results = await asyncio.gather(*(enqueue(i) for i in range(4)))
    assert sum(isinstance(r, Job) for r in results) == 1
    job = next(r for r in results if isinstance(r, Job))
    await jobs.cancel(job.id, actor, "cancel")
    assert (await servers.get(sid))["lifecycle_state"] == "unknown"
    job = await enqueue(10)
    claimed = await jobs.claim(job.id)
    with pytest.raises(CancellationUnsafe):
        await jobs.cancel(job.id, actor, "cancel")
    async with transaction(auth.engine) as db:
        stored = await db.get(Job, claimed.id)
        stored.lease_until = await db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    await jobs.recover()
    assert (await jobs.get(job.id)).status == "queued"
    await due(jobs, job.id)
    await worker.execute(job.id)
    restart = await servers.enqueue(sid, "server.restart", "restart", actor, "restart")
    await jobs.claim(restart.id)
    async with transaction(auth.engine) as db:
        stored = await db.get(Job, restart.id)
        stored.lease_until = await db.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    await jobs.recover()
    assert (await jobs.get(restart.id)).error_code == "unsafe_outcome"
    assert (await servers.get(sid))["lifecycle_state"] == "unknown"


async def test_api_lifecycle_rbac_validation_and_missing_resources(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    app.state.servers = ServerService(auth.engine, key)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, creds, _ = await account(auth)
        admin = await login(client, creds)
        _, creds, _ = await account(auth, "viewer")
        viewer = await login(client, creds)
        for operation in LIFECYCLE:
            body = {"idempotency_key": "test"}
            if operation in ("server.deploy", "server.update"):
                body["version"] = "1.2.3"
            path = f"/api/servers/{uuid4()}/" + operation.removeprefix("server.")
            assert (await client.post(path, json=body)).status_code == 401
            assert (await client.post(path, headers=viewer, json=body)).status_code == 403
            assert (await client.post(path, headers=admin, json=body)).status_code == 404
            assert (
                await client.post(path, headers=admin, json=body | {"command": "id"})
            ).status_code == 422


async def test_preflight_expiration_failure_and_target_changes_invalidate_deploy(auth, key):  # noqa: F811
    from apps.servers.schemas import ServerInput

    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    intent = {"version": "1.2.3", "acme_email": "operator@example.org"}
    await ready(servers, sid, actor, worker)
    async with transaction(auth.engine) as db:
        server = await db.get(Server, sid)
        server.preflight_passed_at -= timedelta(minutes=16)
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.deploy", "stale", actor, "deploy", intent)
    await ready(servers, sid, actor, worker)
    port.outcome = Outcome.FAILED
    await ready(servers, sid, actor, worker)
    assert (await servers.get(sid))["preflight_passed_at"] is None
    port.outcome = Outcome.SUCCEEDED
    await ready(servers, sid, actor, worker)
    body = payload()
    body.pop("credentials")
    await servers.update(sid, ServerInput.model_validate(body), actor, "edit")
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.deploy", "edited", actor, "deploy", intent)


async def test_credentials_and_lifecycle_admission_are_serialized(auth, key):  # noqa: F811
    from apps.vpn.schemas import DeviceInput, UserInput
    from apps.vpn.service import VpnError, VpnService

    actor, servers, sid, jobs, port, worker = await setup(auth, key)
    await ready(servers, sid, actor, worker)
    vpn = VpnService(auth.engine, key)
    user = await vpn.create_user(
        UserInput(display_name="Lifecycle", access_mode="all"), actor, "create"
    )
    device = await vpn.create_device(user["id"], DeviceInput(name="Phone"), actor, "device")
    deploy = await servers.enqueue(
        sid,
        "server.deploy",
        "deploy",
        actor,
        "deploy",
        {"version": "1.2.3", "acme_email": "operator@example.org"},
    )
    with pytest.raises(VpnError):
        await vpn.create_credential(user["id"], device["id"], sid, actor, "create")
    await worker.execute(deploy.id)
    credential_job = await vpn.create_credential(user["id"], device["id"], sid, actor, "create")
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.restart", "active-credential", actor, "restart")
    await jobs.cancel(credential_job.id, actor, "cancel")
    with pytest.raises(ServerError):
        await servers.enqueue(sid, "server.uninstall", "pending-credential", actor, "uninstall")
