"""Browser transport and the read-only Admin Web contracts against real PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.auth import ADMIN_COOKIE
from apps.api.client import CLIENT_COOKIE
from apps.api.client_auth_service import ClientAuthService
from apps.api.main import create_app
from apps.jobs.service import JobService
from apps.persistence.database import transaction
from apps.persistence.models import Admin, AdminSession
from apps.servers.service import ServerService
from apps.vpn.schemas import UserInput
from apps.vpn.service import VpnService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport, create
from tests.test_migrations import database  # noqa: F401

WEB = {"X-TTCP-Admin": "web"}


@pytest.fixture
async def admin_app(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    transport = MemoryTransport()
    app.state.jobs = JobService(auth.engine, transport, transport)
    app.state.vpn = VpnService(auth.engine, key)
    app.state.servers = ServerService(auth.engine, key)
    app.state.client_auth = ClientAuthService(auth.engine)
    return app


async def browser_login(http, credentials):
    return await http.post(
        "/api/auth/login", json=credentials | {"session_mode": "cookie"}, headers=WEB
    )


async def test_admin_cookie_refresh_logout_host_scope_and_cli_compatibility(admin_app, auth):  # noqa: F811
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="https://admin.test"
    ) as http:
        _, credentials, _ = await account(auth)
        issued = await browser_login(http, credentials)
        assert issued.status_code == 200 and "access_token" not in issued.json()
        cookie = issued.headers["set-cookie"]
        for attribute in ("Secure", "HttpOnly", "SameSite=strict", "Path=/;", "Max-Age="):
            assert attribute in cookie
        assert "Domain=" not in cookie
        me = (await http.get("/api/auth/me", headers=WEB)).json()
        async with transaction(auth.engine) as db:
            row = await db.get(AdminSession, UUID(me["session_id"]))
            assert row.token_hash != http.cookies.get(ADMIN_COOKIE)
        async with AsyncClient(
            transport=ASGITransport(app=admin_app), base_url=http.base_url, cookies=http.cookies
        ) as refreshed:
            assert (await refreshed.get("/api/auth/me", headers=WEB)).json() == me
            assert (
                await refreshed.get("https://vpn.test/api/auth/me", headers=WEB)
            ).status_code == 401
            assert (await refreshed.get("/api/client/session", headers=WEB)).status_code == 401
            invalid = await refreshed.get(
                "/api/auth/me", headers=WEB | {"Authorization": "Bearer invalid"}
            )
            assert invalid.status_code == 401
            # Restore a copy to exercise explicit logout despite invalid-cookie clearing.
            refreshed.cookies = http.cookies
            assert (await refreshed.post("/api/auth/logout", headers=WEB)).status_code == 204
        denied = await http.get("/api/auth/me", headers=WEB)
        assert denied.status_code == 401 and "Max-Age=0" in denied.headers["set-cookie"]
        _, cli_credentials, _ = await account(auth)
        cli = await login(http, cli_credentials)
        assert (await http.get("/api/auth/me", headers=cli)).status_code == 200
        assert (
            await http.get("/api/auth/me", headers=WEB | {"Cookie": CLIENT_COOKIE + "=" + "C" * 43})
        ).status_code == 401


async def test_cookie_login_and_mutations_require_same_origin_not_sibling_origin(admin_app, auth):  # noqa: F811
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="https://admin.test:8443"
    ) as http:
        _, credentials, _ = await account(auth)
        body = credentials | {"session_mode": "cookie"}
        assert (await http.post("/api/auth/login", json=body)).status_code == 403
        for hostile in (
            {"Origin": "https://vpn.test:8443"},
            {"Origin": "https://admin.test"},
            {"Origin": "null"},
            {"Origin": "https://["},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ):
            assert (
                await http.post("/api/auth/login", json=body, headers=WEB | hostile)
            ).status_code == 403
        assert (await browser_login(http, credentials)).status_code == 200
        assert (await http.post("/api/vpn-users", json={"display_name": "CSRF"})).status_code == 403
        assert (
            await http.post(
                "/api/vpn-users",
                json={"display_name": "CSRF"},
                headers=WEB | {"Origin": "https://vpn.test:8443"},
            )
        ).status_code == 403
        for method, path in (("GET", "/api/auth/me"), ("POST", "/api/auth/logout")):
            denied = await http.request(method, path, headers=WEB | {"Sec-Fetch-Site": "same-site"})
            assert denied.status_code == 403
        preflight = await http.options(
            "/api/vpn-users",
            headers={"Origin": "https://vpn.test:8443", "Access-Control-Request-Method": "POST"},
        )
        assert "access-control-allow-origin" not in preflight.headers


async def test_viewer_reads_jobs_invitations_devices_but_cannot_mutate(admin_app, auth):  # noqa: F811
    actor, _, _ = await account(auth)
    person = await admin_app.state.vpn.create_user(
        UserInput(display_name="Read only"), actor, "user"
    )
    issued = await admin_app.state.client_auth.create_invitation(person["id"], 300, actor, "invite")
    jobs = [await create(admin_app.state.jobs) for _ in range(3)]
    await admin_app.state.jobs.cancel(jobs[0].id, actor, "cancel")
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="https://admin.test"
    ) as http:
        assert (await http.get("/api/jobs")).status_code == 401
        _, credentials, _ = await account(auth, "viewer")
        assert (await browser_login(http, credentials)).status_code == 200
        listed = await http.get("/api/jobs?limit=2", headers=WEB)
        assert listed.status_code == 200 and len(listed.json()) == 2
        assert listed.json()[0]["id"] == str(jobs[-1].id)
        next_page = await http.get("/api/jobs?limit=2&offset=2", headers=WEB)
        assert next_page.json()[0]["id"] == str(jobs[0].id)
        for query in ("limit=101", "offset=-1", "limit=0"):
            assert (await http.get("/api/jobs?" + query, headers=WEB)).status_code == 422
        base = f"/api/vpn-users/{person['id']}"
        for path in ("/api/servers", "/api/vpn-users", base + "/devices", base + "/invitations"):
            response = await http.get(path, headers=WEB)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert issued["token"] not in response.text and "token_hash" not in response.text
        assert (
            await http.get(base + f"/invitations/{issued['id']}", headers=WEB)
        ).status_code == 200
        assert (await http.get(base + f"/invitations/{uuid4()}", headers=WEB)).status_code == 404
        for method, path, body in (
            ("POST", "/api/vpn-users", {"display_name": "Denied"}),
            ("PUT", base + "/enabled", {"enabled": False}),
            ("PUT", base + "/access", {"access_mode": "all"}),
            ("PATCH", base, {"device_limit": 1}),
            ("POST", base + "/devices", {"name": "Denied"}),
            ("POST", base + "/invitations", {}),
            ("DELETE", base + f"/invitations/{issued['id']}", None),
            ("POST", f"/api/servers/{uuid4()}/preflight", {"idempotency_key": "denied"}),
            ("POST", f"/api/servers/{uuid4()}/status", {"idempotency_key": "denied"}),
            ("POST", f"/api/jobs/{jobs[-1].id}/cancel", None),
        ):
            assert (await http.request(method, path, json=body, headers=WEB)).status_code == 403
        stream = await http.get(f"/api/jobs/{jobs[0].id}/logs", headers=WEB)
        assert stream.status_code == 200 and "event: done" in stream.text


@pytest.mark.parametrize("invalidate", ["expired", "revoked", "disabled", "role"])
async def test_cookie_rechecks_authoritative_state(admin_app, auth, invalidate):  # noqa: F811
    async with AsyncClient(
        transport=ASGITransport(app=admin_app), base_url="https://admin.test"
    ) as http:
        actor, credentials, _ = await account(auth)
        assert (await browser_login(http, credentials)).status_code == 200
        cookie = http.cookies.get(ADMIN_COOKIE)
        async with transaction(auth.engine) as db:
            row = await db.scalar(select(AdminSession).where(AdminSession.admin_id == actor))
            if invalidate == "expired":
                row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            elif invalidate == "revoked":
                row.revoked_at = datetime.now(UTC)
            else:
                identity = await db.get(Admin, actor)
                if invalidate == "disabled":
                    identity.enabled = False
                else:
                    identity.role = "viewer"
        if invalidate == "role":
            assert (await http.get("/api/auth/me", headers=WEB)).json()["role"] == "viewer"
            assert (
                await http.post("/api/vpn-users", headers=WEB, json={"display_name": "Denied"})
            ).status_code == 403
            assert (
                await http.put(
                    f"/api/notifications/{uuid4()}/read", headers=WEB, json={"read": True}
                )
            ).status_code == 403
        else:
            for path in ("/api/auth/me", "/api/monitoring", "/api/audit", "/api/notifications"):
                denied = await http.get(path, headers=WEB | {"Cookie": f"{ADMIN_COOKIE}={cookie}"})
                assert denied.status_code == 401 and "Max-Age=0" in denied.headers["set-cookie"]
                assert denied.headers["cache-control"] == "no-store"
