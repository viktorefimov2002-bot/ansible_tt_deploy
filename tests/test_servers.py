import asyncio
import json
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from apps.api.main import create_app
from apps.execution.jobs import execution_handlers
from apps.execution.ports import CHECKS, ExecutionResult, Outcome
from apps.jobs.service import JobService, LostClaim
from apps.jobs.worker import Worker
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, Server
from apps.servers.schemas import CreateServer
from apps.servers.service import ServerError, ServerService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport
from tests.test_migrations import database  # noqa: F401


def payload():
    identity = Ed25519PrivateKey.generate()
    private = identity.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    public = (
        identity.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    return dict(
        name="node-1",
        hostname="node.example.org",
        public_ip="192.0.2.5",
        domain="vpn.example.org",
        ssh_user="manager",
        credentials={"private_key": private, "host_key": public},
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"ssh_user": "root"},
        {"ssh_port": 0},
        {"ssh_port": True},
        {"name": " "},
        {"hostname": "host;id"},
        {"hostname": "fe80::1%eth0"},
        {"public_ip": "fe80::1%eth0"},
        {"hostname": "{{secret}}"},
        {"public_ip": "garbage"},
        {"domain": "::1"},
        {"domain": "192.0.2.1"},
        {"status": "healthy"},
        {"password": "do-not-echo"},
        {"credentials": {"host_key": "bad", "private_key": "secret"}},
    ],
)
def test_server_input_rejects_unsafe_or_owned_fields(changes):
    with pytest.raises(ValidationError):
        CreateServer.model_validate(payload() | changes)


async def test_server_api_rbac_lifecycle_and_redaction(auth, key):  # noqa: F811
    app = create_app()
    transport = MemoryTransport()
    app.state.auth = auth
    app.state.jobs = JobService(auth.engine, transport, transport)
    servers = app.state.servers = ServerService(auth.engine, key)
    body = payload()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, credentials, _ = await account(auth)
        admin = await login(client, credentials)
        _, credentials, _ = await account(auth, "viewer")
        viewer = await login(client, credentials)
        assert (await client.get("/api/servers")).status_code == 401
        assert (await client.post("/api/servers", json=body)).status_code == 401
        assert (await client.post("/api/servers", headers=viewer, json=body)).status_code == 403
        response = await client.post("/api/servers", headers=admin, json=body)
        assert response.status_code == 201, response.text
        node = response.json()
        path = "/api/servers/" + node["id"]
        assert "private_key" not in response.text and "ciphertext" not in response.text
        assert node["host_key_fingerprint"].startswith("SHA256:")
        assert (await client.get(path, headers=viewer)).status_code == 200
        assert len((await client.get("/api/servers", headers=viewer)).json()) == 1
        assert (await client.post("/api/servers", headers=admin, json=body)).status_code == 409
        update = {k: v for k, v in body.items() if k != "credentials"}
        for method, suffix, data in (
            ("PUT", "", update),
            ("PUT", "/enabled", {"enabled": False}),
            ("PUT", "/credentials", body["credentials"]),
            ("POST", "/status", {"idempotency_key": "test"}),
            ("POST", "/preflight", {"idempotency_key": "test"}),
            ("DELETE", "", None),
        ):
            assert (await client.request(method, path + suffix, json=data)).status_code == 401
            assert (
                await client.request(method, path + suffix, headers=viewer, json=data)
            ).status_code == 403
        bad = await client.put(
            path + "/credentials",
            headers=admin,
            json={"host_key": "bad", "private_key": "private-secret"},
        )
        assert bad.status_code == 422 and "private-secret" not in bad.text
        assert (await client.get(f"/api/servers/{uuid4()}", headers=admin)).status_code == 404
        assert (
            await client.put(path, headers=admin, json=update | {"location": "EU"})
        ).status_code == 200
        assert (
            await client.put(path + "/enabled", headers=admin, json={"enabled": False})
        ).status_code == 200
        assert (
            await client.post(path + "/status", headers=admin, json={"idempotency_key": "disabled"})
        ).status_code == 409
        await client.put(path + "/enabled", headers=admin, json={"enabled": True})
        rotated = payload()["credentials"]
        assert (
            await client.put(path + "/credentials", headers=admin, json=rotated)
        ).status_code == 200
        job = (
            await client.post(path + "/status", headers=admin, json={"idempotency_key": "status"})
        ).json()
        repeated = (
            await client.post(path + "/status", headers=admin, json={"idempotency_key": "status"})
        ).json()
        assert job["id"] == repeated["id"]
        assert (await client.put(path, headers=admin, json=update)).status_code == 409
        assert (
            await client.put(path + "/enabled", headers=admin, json={"enabled": False})
        ).status_code == 409
        resolved = await servers.resolve(UUID(node["id"]))
        assert resolved.target.private_key.get_secret_value() == rotated["private_key"]
        assert "PRIVATE KEY" not in str(job) + resolved.model_dump_json()
        async with transaction(auth.engine) as db:
            saved = await db.get(Server, UUID(node["id"]))
            assert b"PRIVATE KEY" not in saved.ssh_private_ciphertext
            audit = (await db.scalars(select(AuditEvent))).all()
            assert "PRIVATE KEY" not in str([a.details for a in audit])
            assert any(a.action == "server.credentials.rotate" for a in audit)


async def test_diagnostic_job_partial_results_duplicate_fencing_and_disable(auth, key):  # noqa: F811
    servers = ServerService(auth.engine, key)
    actor, _, _ = await account(auth)
    node = await servers.create(CreateServer.model_validate(payload()), actor, "create")
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    checks = dict.fromkeys(CHECKS, "unknown") | {"ssh": "pass", "disk": "fail"}
    calls = []

    class Port:
        async def execute(self, request, emit):
            calls.append(request)
            return ExecutionResult(Outcome.TIMED_OUT, checks=checks)

    async def enqueue():
        return await servers.enqueue(node["id"], "server.preflight", "same", actor, "request")

    first, second = await asyncio.gather(enqueue(), enqueue())
    assert first.id == second.id
    transport.available = False
    worker = Worker(jobs, execution_handlers(Port(), servers))
    await worker.execute(first.id)
    await worker.execute(first.id)
    job = await jobs.get(first.id)
    assert job.status == "succeeded" and len(calls) == 1
    assert job.result["checks"] == checks and job.result["ready"] is False
    assert job.result["outcome"] == "execution_timeout"
    assert (await servers.get(node["id"]))["status"] == "reachable"
    with pytest.raises(LostClaim):
        await jobs.execution_result(first.id, uuid4(), ExecutionResult(Outcome.SUCCEEDED))
    assert "PRIVATE KEY" not in json.dumps(job.result) + str(job.history)
    from apps.servers.schemas import Enabled

    await servers.update(node["id"], Enabled(enabled=False), actor, "disable")
    with pytest.raises(ServerError):
        await servers.enqueue(node["id"], "server.preflight", "next", actor, "request")


async def test_preflight_retry_and_bad_encryption_fail_closed(auth, key):  # noqa: F811
    from cryptography.fernet import Fernet

    from apps.jobs.worker import RetryableError
    from tests.test_jobs import due

    servers = ServerService(auth.engine, key)
    actor, _, _ = await account(auth)
    node = await servers.create(CreateServer.model_validate(payload()), actor, "create")
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    job = await servers.enqueue(node["id"], "server.preflight", "retry", actor, "request")
    calls = []

    class Port:
        async def execute(self, request, emit):
            calls.append(request)
            if len(calls) == 1:
                raise RetryableError("private-secret")
            return ExecutionResult(Outcome.SUCCEEDED, checks=dict.fromkeys(CHECKS, "pass"))

    worker = Worker(jobs, execution_handlers(Port(), servers))
    await worker.execute(job.id)
    assert (await jobs.get(job.id)).status == "queued"
    await due(jobs, job.id)
    await worker.execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == "succeeded" and saved.result["attempt"] == 2
    assert saved.result["ready"] is True and "private-secret" not in str(saved.history)
    job = await servers.enqueue(node["id"], "server.status", "bad-key", actor, "request")
    wrong = ServerService(auth.engine, Fernet.generate_key().decode())
    await Worker(jobs, execution_handlers(Port(), wrong)).execute(job.id)
    assert (await jobs.get(job.id)).error_code == "execution_invalid"
    assert len(calls) == 2
