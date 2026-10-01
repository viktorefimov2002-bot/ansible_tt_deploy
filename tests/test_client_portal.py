"""Client journey, durable state and fail-closed configuration delivery."""

import asyncio
import json
import runpy
import sys
import types
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.client_auth_service import ClientAuthService
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
from apps.persistence.models import AuditEvent, DeviceCredential, Server, VpnUser
from apps.servers.schemas import CreateServer
from apps.servers.service import ServerService
from apps.vpn.jobs import credential_handlers
from apps.vpn.schemas import AccessInput, DeviceInput, UserInput
from apps.vpn.service import VpnService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_client import RateRedis
from tests.test_jobs import MemoryTransport
from tests.test_migrations import database  # noqa: F401
from tests.test_servers import payload

CONFIG = {"toml": "# synthetic endpoint-generated TOML", "deep_link": "tt://synthetic-test-only"}


async def test_invite_to_configuration_and_access_invalidation(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    app.state.client_auth = ClientAuthService(auth.engine)
    vpn = app.state.vpn = VpnService(auth.engine, key)
    app.state.dependencies = type("Deps", (), {"redis": RateRedis()})()
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    actor, credentials, _ = await account(auth)
    server = await ServerService(auth.engine, key).create(
        CreateServer.model_validate(payload()), actor, "server"
    )
    user = await vpn.create_user(
        UserInput(display_name="Portal", device_limit=1, access_mode="all"), actor, "user"
    )
    other = await vpn.create_user(UserInput(display_name="Other"), actor, "other")
    foreign = await vpn.create_device(other["id"], DeviceInput(name="Other"), actor, "other")

    class Port:
        async def execute(self, request, emit):
            assert request.credential.public_address == server["domain"]
            return ExecutionResult(Outcome.SUCCEEDED, configuration=CONFIG)

    worker = Worker(jobs, credential_handlers(Port(), vpn))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
        admin = await login(http, credentials)
        _, viewer_body, _ = await account(auth, role="viewer")
        viewer = await login(http, viewer_body)
        invite = (
            await http.post(f"/api/vpn-users/{user['id']}/invitations", headers=admin, json={})
        ).json()
        exchange = await http.post("/api/client/exchange", json={"token": invite["token"]})
        headers = {"Authorization": "Bearer " + exchange.json()["access_token"]}
        device = (
            await http.post("/api/client/devices", json={"name": "Phone"}, headers=headers)
        ).json()
        base = f"/api/client/devices/{device['id']}"
        config_path = base + f"/configurations/{server['id']}"
        for forbidden in ({}, admin, viewer):
            response = await http.post(config_path, headers=forbidden)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
        assert (await http.post(config_path, headers=headers)).status_code == 409
        assert (
            await http.post(
                f"/api/client/devices/{foreign['id']}/configurations/{server['id']}",
                headers=headers,
            )
        ).status_code == 404
        job = (await http.post(base + f"/credentials/{server['id']}", headers=headers)).json()
        states_path = base + "/provisioning"
        assert (await http.get(states_path, headers=headers)).json()[0]["state"] == "pending"
        # Status polling must not wait on the worker's remote-execution user lock.
        async with transaction(auth.engine) as locked:
            await locked.scalar(select(VpnUser).where(VpnUser.id == user["id"]).with_for_update())
            response = await asyncio.wait_for(http.get(states_path, headers=headers), 1)
            assert response.status_code == 200
        claim = await jobs.claim(UUID(job["id"]))
        assert (await http.get(states_path, headers=headers)).json()[0]["state"] == "running"
        await jobs.complete(UUID(job["id"]), claim.claim_token, code="handler_failed")
        assert (await http.get(states_path, headers=headers)).json()[0]["state"] == "failed"
        retry = (await http.post(base + f"/credentials/{server['id']}", headers=headers)).json()
        await worker.execute(UUID(retry["id"]))
        assert (await jobs.get(UUID(retry["id"]))).status == "succeeded"
        assert (await http.get(states_path, headers=headers)).json()[0]["state"] == "ready"
        for _ in range(2):
            response = await http.post(config_path, headers=headers)
            assert response.status_code == 200 and response.json() == CONFIG
            assert response.headers["cache-control"] == "no-store"
        downloaded = await http.post(config_path + "?format=toml", headers=headers)
        assert downloaded.text == CONFIG["toml"]
        assert downloaded.headers["content-type"].startswith("application/toml")
        assert downloaded.headers["cache-control"] == "no-store"
        assert "attachment" in downloaded.headers["content-disposition"]
        assert (await http.get("/api/client/me", headers=headers)).json()["devices_used"] == 1
        async with transaction(auth.engine) as db:
            encrypted = await db.scalar(select(DeviceCredential.config_ciphertext))
            assert CONFIG["toml"].encode() not in encrypted
            events = (await db.scalars(select(AuditEvent))).all()
            assert any(e.action == "client.configuration.deliver" for e in events)
            assert CONFIG["deep_link"] not in str([e.details for e in events])
            node = await db.get(Server, server["id"])
            node.enabled = False
        assert (await http.post(config_path, headers=headers)).status_code == 404
        async with transaction(auth.engine) as db:
            node = await db.get(Server, server["id"])
            node.enabled = True
        await vpn.set_access(
            user["id"], AccessInput(access_mode="selected", server_ids=[]), actor, "remove-access"
        )
        assert (await http.post(config_path, headers=headers)).status_code == 403
        await vpn.set_access(user["id"], AccessInput(access_mode="all"), actor, "restore")
        assert (await http.post(config_path, headers=headers)).status_code == 409
        async with transaction(auth.engine) as db:
            saved_user = await db.get(VpnUser, user["id"])
            saved_user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        assert (await http.post(config_path, headers=headers)).status_code == 401
        async with transaction(auth.engine) as db:
            saved_user = await db.get(VpnUser, user["id"])
            saved_user.expires_at = None
        await http.delete(base, headers=headers)
        assert (await http.post(config_path, headers=headers)).status_code == 409
        await vpn.set_enabled(user["id"], False, actor, "disable")
        assert (await http.post(config_path, headers=headers)).status_code == 401
        assert (
            await http.post(
                f"/api/client/devices/{uuid4()}/configurations/{server['id']}", headers=headers
            )
        ).status_code == 401


async def test_adapter_configuration_uses_private_file_and_never_events():
    async def runner(argv, cwd, env, output, deadline):
        (cwd / "delivery.json").write_text(json.dumps(CONFIG))
        return 0

    request = ExecutionRequest(
        operation="credential.create",
        target=Target(
            host="node.example.org",
            user="manager",
            host_key="ssh-ed25519 AAAA",
            private_key="test-only",
        ),
        credential=CredentialParameters(
            username="ttcp_" + "a" * 32, password="p" * 48, public_address="vpn.example.org"
        ),
    )
    events = []

    async def emit(event):
        events.append(event)

    result = await AnsibleExecutionAdapter(runner=runner).execute(request, emit)
    assert result.configuration == CONFIG
    assert "synthetic" not in str(events) and "synthetic" not in repr(result)


def test_node_generation_delegates_both_formats_to_official_binary(monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", types.ModuleType("fcntl"))
    helper = runpy.run_path("scripts/node_credential.py", run_name="node_test")
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        fmt = argv[argv.index("--format") + 1]
        return types.SimpleNamespace(
            returncode=0, stdout=(CONFIG["toml"] if fmt == "toml" else CONFIG["deep_link"]).encode()
        )

    monkeypatch.setattr(helper["subprocess"], "run", run)
    assert helper["generate_configuration"]("ttcp_" + "a" * 32, "vpn.example.org") == CONFIG
    assert [args[args.index("--format") + 1] for args in calls] == ["toml", "deeplink"]
    assert all(
        args[0].replace("\\", "/") == "/opt/trusttunnel/trusttunnel_endpoint" for args in calls
    )
    with pytest.raises(helper["Rejected"]):
        monkeypatch.setattr(
            helper["subprocess"],
            "run",
            lambda *a, **kw: types.SimpleNamespace(returncode=1, stdout=b""),
        )
        helper["generate_configuration"]("ttcp_" + "a" * 32, "vpn.example.org")
