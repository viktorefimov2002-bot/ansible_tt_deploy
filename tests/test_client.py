"""Client invitation, session, isolation and self-service contracts."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.client_auth_service import ClientAuthService
from apps.api.main import create_app
from apps.persistence.database import transaction
from apps.persistence.models import AuditEvent, ClientSession, Invitation, VpnUser
from apps.servers.schemas import CreateServer
from apps.servers.service import ServerService
from apps.vpn.schemas import AccessInput, DeviceInput, UserInput
from apps.vpn.service import VpnService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_migrations import database  # noqa: F401
from tests.test_servers import payload


class RateRedis:
    def __init__(self):
        self.count = 0

    async def eval(self, script, count, rate_key):
        assert count == 1 and rate_key.startswith("ttcp:client:exchange:")
        self.count += 1
        return self.count


async def test_client_auth_and_owned_api(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    app.state.client_auth = ClientAuthService(auth.engine, 300)
    app.state.vpn = VpnService(auth.engine, key)
    app.state.dependencies = type("Deps", (), {"redis": RateRedis()})()
    actor, credentials, _ = await account(auth)
    _, viewer_credentials, _ = await account(auth, role="viewer")
    node = await ServerService(auth.engine, key).create(
        CreateServer.model_validate(payload()), actor, "server"
    )
    first = await app.state.vpn.create_user(
        UserInput(display_name="First", device_limit=1, access_mode="all"), actor, "first"
    )
    second = await app.state.vpn.create_user(
        UserInput(display_name="Second", access_mode="all"), actor, "second"
    )
    other_device = await app.state.vpn.create_device(
        second["id"], DeviceInput(name="Other phone"), actor, "other"
    )
    base = f"/api/vpn-users/{first['id']}/invitations"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
        admin = await login(http, credentials)
        viewer = await login(http, viewer_credentials)
        assert (await http.post(base, headers=viewer, json={})).status_code == 403
        issued = await http.post(base, headers=admin, json={})
        assert issued.status_code == 201 and issued.headers["cache-control"] == "no-store"
        invitation = issued.json()
        token = invitation.pop("token")
        assert "token" not in str((await http.get(base, headers=admin)).json())
        assert "token" not in str(
            (await http.get(base + "/" + invitation["id"], headers=admin)).json()
        )
        assert (
            await http.post("/api/client/exchange", json={"token": token[:-1] + "!"})
        ).status_code == 401
        exchanged = await http.post("/api/client/exchange", json={"token": token})
        assert exchanged.status_code == 200
        assert exchanged.headers["cache-control"] == "no-store"
        client_headers = {"Authorization": "Bearer " + exchanged.json()["access_token"]}
        assert (await http.post("/api/client/exchange", json={"token": token})).status_code == 401
        assert (await http.get("/api/client/me", headers=admin)).status_code == 401
        assert (await http.get("/api/auth/me", headers=client_headers)).status_code == 401
        profile = (await http.get("/api/client/me", headers=client_headers)).json()
        assert profile["device_limit"] == 1 and profile["devices_used"] == 0
        servers = (await http.get("/api/client/servers", headers=client_headers)).json()
        assert servers[0]["id"] == str(node["id"])
        device = (
            await http.post("/api/client/devices", headers=client_headers, json={"name": "Phone"})
        ).json()
        requested = await http.post(
            f"/api/client/devices/{device['id']}/credentials/{node['id']}",
            headers=client_headers,
        )
        assert requested.status_code == 202
        assert set(requested.json()) == {"id", "status"}
        state = (
            await http.get(
                f"/api/client/devices/{device['id']}/credentials", headers=client_headers
            )
        ).json()
        assert len(state) == 1 and state[0]["status"] == "pending"
        assert "password" not in str(state) + requested.text
        await app.state.vpn.set_access(
            first["id"], AccessInput(access_mode="selected"), actor, "remove-access"
        )
        assert (await http.get("/api/client/servers", headers=client_headers)).json() == []
        assert (
            await http.post(
                f"/api/client/devices/{device['id']}/credentials/{node['id']}",
                headers=client_headers,
            )
        ).status_code == 403
        assert (
            await http.post("/api/client/devices", headers=client_headers, json={"name": "Tablet"})
        ).status_code == 409
        assert (
            await http.patch(
                f"/api/vpn-users/{first['id']}", headers=client_headers, json={"device_limit": 5}
            )
        ).status_code == 401
        assert (
            await http.put(
                f"/api/vpn-users/{first['id']}/access",
                headers=client_headers,
                json={"access_mode": "all"},
            )
        ).status_code == 401
        assert (
            await http.get(f"/api/vpn-users/{second['id']}", headers=client_headers)
        ).status_code == 401
        assert (
            await http.get(f"/api/client/devices/{uuid4()}/credentials", headers=client_headers)
        ).status_code == 404
        assert (
            await http.get(
                f"/api/client/devices/{other_device['id']}/credentials", headers=client_headers
            )
        ).status_code == 404
        assert (
            await http.post(
                f"/api/client/devices/{device['id']}/credentials/{uuid4()}", headers=client_headers
            )
        ).status_code == 404
        assert (
            await http.delete(f"/api/client/devices/{uuid4()}", headers=client_headers)
        ).status_code == 404
        assert (
            await http.delete(f"/api/client/devices/{other_device['id']}", headers=client_headers)
        ).status_code == 404
        assert (
            await http.delete(f"/api/client/devices/{device['id']}", headers=client_headers)
        ).status_code == 200
        assert (await http.post("/api/client/logout", headers=client_headers)).status_code == 204
        assert (await http.get("/api/client/me", headers=client_headers)).status_code == 401
        async with transaction(auth.engine) as db:
            saved = await db.get(Invitation, UUID(invitation["id"]))
            assert saved.token_hash != token and saved.used_at is not None
            session = await db.scalar(
                select(ClientSession).where(ClientSession.user_id == first["id"])
            )
            assert session.token_hash != exchanged.json()["access_token"]
            audit = (await db.scalars(select(AuditEvent))).all()
            assert token not in str([item.details for item in audit])
            assert any(
                item.actor_type == "vpn_user" and item.action == "device.create" for item in audit
            )


async def test_invitation_replay_expiry_revoke_race_and_disable(auth, key):  # noqa: F811
    vpn = VpnService(auth.engine, key)
    client_auth = ClientAuthService(auth.engine, 300)
    actor, _, _ = await account(auth)
    user = await vpn.create_user(UserInput(display_name="Invitee"), actor, "user")

    async def issue():
        return await client_auth.create_invitation(user["id"], 300, actor, "issue")

    invitation = await issue()
    token = invitation["token"]
    results = await asyncio.gather(*(client_auth.exchange(token, "race") for _ in range(2)))
    assert sum(result is not None for result in results) == 1
    session_token = next(result[0] for result in results if result)
    assert await client_auth.authenticate(session_token) is not None
    async with transaction(auth.engine) as db:
        session = await db.scalar(select(ClientSession).where(ClientSession.user_id == user["id"]))
        session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await client_auth.authenticate(session_token) is None
    async with transaction(auth.engine) as db:
        session = await db.scalar(select(ClientSession).where(ClientSession.user_id == user["id"]))
        session.expires_at = datetime.now(UTC) + timedelta(seconds=300)
    async with transaction(auth.engine) as db:
        saved_user = await db.get(VpnUser, user["id"])
        saved_user.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await client_auth.authenticate(session_token) is None
    async with transaction(auth.engine) as db:
        saved_user = await db.get(VpnUser, user["id"])
        saved_user.expires_at = None
    assert await client_auth.authenticate(session_token) is not None
    assert await client_auth.exchange(token, "replay") is None
    revoked = await issue()
    assert await client_auth.revoke_invitation(user["id"], revoked["id"], actor, "revoke")
    assert await client_auth.exchange(revoked["token"], "revoked") is None
    expired = await issue()
    async with transaction(auth.engine) as db:
        row = await db.get(Invitation, expired["id"])
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await client_auth.exchange(expired["token"], "expired") is None
    assert await client_auth.exchange("A" * 43, "unknown") is None
    outstanding = await issue()
    await vpn.set_enabled(user["id"], False, actor, "disable")
    assert await client_auth.authenticate(session_token) is None
    await vpn.set_enabled(user["id"], True, actor, "enable")
    assert await client_auth.authenticate(session_token) is None
    assert await client_auth.exchange(outstanding["token"], "reenabled") is None


async def test_exchange_rate_limit_and_storage_failure():
    app = create_app()
    limiter = RateRedis()
    app.state.dependencies = type("Deps", (), {"redis": limiter})()

    class FakeAuth:
        async def exchange(self, token, request_id):
            return None

    app.state.client_auth = FakeAuth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
        for _ in range(30):
            response = await http.post("/api/client/exchange", json={"token": "A" * 43})
            assert response.status_code == 401
        response = await http.post("/api/client/exchange", json={"token": "A" * 43})
        assert response.status_code == 429 and response.headers["cache-control"] == "no-store"

        class BrokenRedis:
            async def eval(self, *args):
                raise RuntimeError("unavailable")

        app.state.dependencies.redis = BrokenRedis()
        response = await http.post("/api/client/exchange", json={"token": "A" * 43})
        assert response.status_code == 503
