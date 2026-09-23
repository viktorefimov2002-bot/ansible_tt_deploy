"""VPN identity, quota, access and credential lifecycle contracts."""

import asyncio
import json
import sys
import types
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.main import create_app
from apps.execution.ansible import AnsibleExecutionAdapter
from apps.execution.ports import (
    CredentialParameters,
    ExecutionRequest,
    ExecutionResult,
    Outcome,
    Target,
)
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, DeviceCredential
from apps.servers.schemas import CreateServer
from apps.servers.service import ServerService
from apps.vpn.jobs import credential_handlers
from apps.vpn.service import VpnService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport, due
from tests.test_migrations import database  # noqa: F401
from tests.test_servers import payload


def test_typed_credential_execution_and_redacted_adapter():
    async def exercise():
        calls = []

        async def runner(argv, cwd, env, output, deadline):
            params = json.loads((cwd / "parameters.json").read_text())
            calls.append((argv, params))
            await output(b"unexpected secret in output\n", False)
            return 0

        port = AnsibleExecutionAdapter(runner=runner)
        request = ExecutionRequest(
            operation="credential.create",
            target=Target(
                host="node.example.org",
                user="manager",
                host_key="ssh-ed25519 AAAA",
                private_key="test-private",
            ),
            credential=CredentialParameters(
                username="ttcp_" + "a" * 32, password="test-secret-" * 4
            ),
        )
        events = []

        async def emit(event):
            events.append(event.value)

        result = await port.execute(request, emit)
        assert result.outcome == Outcome.SUCCEEDED
        assert calls[0][0][-1].endswith("managed-credential.yml")
        assert calls[0][1]["ttcp_credential_payload"]["password"] == "test-secret-" * 4
        assert "test-secret" not in str(events) + request.model_dump_json()
        assert events[-1] == "execution_output_suppressed"

    asyncio.run(exercise())
    with pytest.raises(ValidationError):
        ExecutionRequest(
            operation="credential.create", credential=CredentialParameters(username="bad")
        )


def test_node_helper_rejects_unknown_credential_format(monkeypatch):
    # The helper runs on Linux; only its deterministic parser is imported here.
    monkeypatch.setitem(sys.modules, "fcntl", types.ModuleType("fcntl"))
    import runpy

    namespace = runpy.run_path("scripts/node_credential.py", run_name="node_test")
    parse, render, rejected = (namespace[x] for x in ("parse", "render", "Rejected"))
    original = {"legacy": "old", "ttcp_" + "a" * 32: "new"}
    assert parse(render(original)) == original
    with pytest.raises(rejected):
        parse('[[client]]\nusername = "a"\npassword = "b"\nunsafe = "x"\n')


async def test_vpn_api_quota_access_rbac_jobs_and_secret_handoff(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    transport = MemoryTransport()
    jobs = app.state.jobs = JobService(auth.engine, transport, transport)
    vpn = app.state.vpn = VpnService(auth.engine, key)
    servers = ServerService(auth.engine, key)
    actor, _, _ = await account(auth)
    first = await servers.create(CreateServer.model_validate(payload()), actor, "create-1")
    second_payload = payload() | {"name": "node-2", "hostname": "node-2.example.org"}
    second = await servers.create(CreateServer.model_validate(second_payload), actor, "create-2")
    calls = []

    class Port:
        async def execute(self, request, emit):
            calls.append(request)
            if len(calls) == 1:
                return ExecutionResult(Outcome.UNREACHABLE)
            return ExecutionResult(Outcome.SUCCEEDED)

    worker = Worker(jobs, credential_handlers(Port(), vpn))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, credentials, _ = await account(auth, username="admin2")
        admin = await login(client, credentials)
        _, credentials, _ = await account(auth, role="viewer")
        viewer = await login(client, credentials)
        body = {"display_name": "Alice", "device_limit": 1, "access_mode": "all"}
        assert (await client.post("/api/vpn-users", json=body)).status_code == 401
        assert (await client.post("/api/vpn-users", json=body, headers=viewer)).status_code == 403
        user = (await client.post("/api/vpn-users", json=body, headers=admin)).json()
        base = "/api/vpn-users/" + user["id"]
        assert (await client.get(base, headers=viewer)).status_code == 200
        assert (
            await client.post(base + "/devices", json={"name": "Other"}, headers=viewer)
        ).status_code == 403
        device = (
            await client.post(base + "/devices", json={"name": "Phone"}, headers=admin)
        ).json()
        assert (
            await client.post(base + "/devices", json={"name": "Tablet"}, headers=admin)
        ).status_code == 409
        assert (
            await client.patch(base, json={"device_limit": 0}, headers=admin)
        ).status_code == 409
        jobs_by_server = []
        for server in (first, second):
            path = base + "/devices/" + device["id"] + "/credentials/" + str(server["id"])
            response = await client.post(path, headers=admin)
            assert response.status_code == 202, response.text
            assert "password" not in response.text and "ciphertext" not in response.text
            jobs_by_server.append((path, UUID(response.json()["id"])))
        assert jobs_by_server[0][1] == UUID(
            (await client.post(jobs_by_server[0][0], headers=admin)).json()["id"]
        )
        await worker.execute(jobs_by_server[0][1])
        assert (await jobs.get(jobs_by_server[0][1])).status == "queued"
        await due(jobs, jobs_by_server[0][1])
        await worker.execute(jobs_by_server[0][1])
        await worker.execute(jobs_by_server[1][1])
        assert (await jobs.get(jobs_by_server[0][1])).status == "succeeded"
        first_download = await client.post(jobs_by_server[0][0] + "/download", headers=admin)
        assert first_download.status_code == 200
        secret = first_download.json()["password"]
        assert (
            await client.post(jobs_by_server[0][0] + "/download", headers=admin)
        ).status_code == 409
        assert (
            await client.post(jobs_by_server[1][0] + "/download", headers=viewer)
        ).status_code == 403
        assert (
            await client.put(
                base + "/access",
                headers=admin,
                json={"access_mode": "selected", "server_ids": [str(first["id"])]},
            )
        ).status_code == 200
        async with transaction(auth.engine) as db:
            rows = (await db.scalars(select(DeviceCredential))).all()
            assert len(rows) == 2 and {r.status for r in rows} == {"active", "revoking"}
            audit = (await db.scalars(select(AuditEvent))).all()
            assert secret not in str([a.details for a in audit])
            ciphertexts = (
                await db.scalars(
                    select(DeviceCredential.secret_ciphertext).where(
                        DeviceCredential.status == "active"
                    )
                )
            ).all()
            assert all(secret.encode() not in value for value in ciphertexts)
        assert (await client.get(base + "/devices", headers=admin)).json()[0]["enabled"]
        await client.delete(base + "/devices/" + device["id"], headers=admin)
        async with transaction(auth.engine) as db:
            rows = (await db.scalars(select(DeviceCredential))).all()
            assert all(r.status == "revoking" for r in rows)
        assert (
            await client.post(base + "/devices", json={"name": "Replacement"}, headers=admin)
        ).status_code == 201
        assert "test-secret" not in str(
            [j.history for j in [await jobs.get(jid) for _, jid in jobs_by_server]]
        )


async def test_default_quota_and_concurrent_device_creation(auth, key):  # noqa: F811
    from apps.vpn.schemas import DeviceInput, UserInput

    vpn = VpnService(auth.engine, key)
    actor, _, _ = await account(auth)
    user = await vpn.create_user(UserInput(display_name="Default"), actor, "create")

    async def add(index):
        try:
            return await vpn.create_device(
                user["id"], DeviceInput(name=f"Device {index}"), actor, "add"
            )
        except Exception as exc:
            return exc

    results = await asyncio.gather(*(add(i) for i in range(5)))
    assert sum(isinstance(x, dict) for x in results) == 3
    assert len(await vpn.list_devices(user["id"])) == 3


async def test_device_revoke_disable_expiration_and_revoke_retry(auth, key):  # noqa: F811
    from datetime import UTC, datetime, timedelta

    from apps.persistence.models import VpnUser
    from apps.vpn.schemas import DeviceInput, UserInput

    actor, _, _ = await account(auth)
    server = await ServerService(auth.engine, key).create(
        CreateServer.model_validate(payload()), actor, "create"
    )
    vpn = VpnService(auth.engine, key)
    user = await vpn.create_user(
        UserInput(display_name="Two devices", access_mode="all"), actor, "create"
    )
    first = await vpn.create_device(user["id"], DeviceInput(name="Phone"), actor, "first")
    second = await vpn.create_device(user["id"], DeviceInput(name="Laptop"), actor, "second")
    first_job = await vpn.create_credential(user["id"], first["id"], server["id"], actor, "first")
    second_job = await vpn.create_credential(
        user["id"], second["id"], server["id"], actor, "second"
    )
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    failures = {"revoke": 1}

    class Port:
        async def execute(self, request, emit):
            if request.operation == "credential.revoke" and failures["revoke"]:
                failures["revoke"] -= 1
                return ExecutionResult(Outcome.UNREACHABLE)
            return ExecutionResult(Outcome.SUCCEEDED)

    worker = Worker(jobs, credential_handlers(Port(), vpn))
    await worker.execute(first_job.id)
    await worker.execute(second_job.id)
    await vpn.revoke_device(user["id"], first["id"], actor, "revoke-device")
    credentials = await vpn.list_credentials(user["id"], second["id"])
    assert credentials[0]["status"] == "active"
    revoke_job, _ = await vpn.revoke_credential(
        user["id"], first["id"], server["id"], actor, "repeat"
    )
    await worker.execute(revoke_job.id)
    assert (await jobs.get(revoke_job.id)).status == "queued"
    await due(jobs, revoke_job.id)
    await worker.execute(revoke_job.id)
    assert (await jobs.get(revoke_job.id)).status == "succeeded"
    assert (await vpn.list_credentials(user["id"], first["id"]))[0]["status"] == "revoked"
    assert (await vpn.list_credentials(user["id"], second["id"]))[0]["status"] == "active"
    await vpn.set_enabled(user["id"], False, actor, "disable")
    assert (await vpn.list_credentials(user["id"], second["id"]))[0]["status"] == "revoking"
    await vpn.set_enabled(user["id"], True, actor, "enable")
    async with transaction(auth.engine) as db:
        saved = await db.get(VpnUser, user["id"])
        saved.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await vpn.expire_due() == 1
    assert (await vpn.get_user(user["id"]))["enabled"] is False
    assert await vpn.expire_due() == 0
