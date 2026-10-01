"""Real PostgreSQL client cookies, refresh, CSRF and admin/client isolation."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from apps.api.client import CLIENT_COOKIE
from apps.api.client_auth_service import ClientAuthService
from apps.api.main import create_app
from apps.persistence.database import transaction
from apps.persistence.models import ClientSession, VpnUser
from apps.vpn.schemas import UserInput
from apps.vpn.service import VpnService
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_client import RateRedis
from tests.test_migrations import database  # noqa: F401

PORTAL = {"X-TTCP-Client": "portal"}


@pytest.fixture
async def cookie_app(auth, key):  # noqa: F811
    app = create_app()
    app.state.auth = auth
    app.state.client_auth = ClientAuthService(auth.engine, 300)
    app.state.vpn = VpnService(auth.engine, key)
    app.state.dependencies = type("Deps", (), {"redis": RateRedis()})()
    actor, _, _ = await account(auth)
    user = await app.state.vpn.create_user(UserInput(display_name="Cookie client"), actor, "user")
    return app, actor, user


async def issue(http, app, actor, user):
    invite = await app.state.client_auth.create_invitation(user["id"], 300, actor, "invite")
    return await http.post(
        "/api/client/exchange",
        headers=PORTAL,
        json={"token": invite["token"], "session_mode": "cookie"},
    )


async def test_cookie_refresh_logout_and_domain_isolation(cookie_app, auth):  # noqa: F811
    app, actor, user = cookie_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://portal.test:8443"
    ) as http:
        exchanged = await issue(http, app, actor, user)
        assert exchanged.status_code == 200
        assert "access_token" not in exchanged.json()
        assert exchanged.headers["cache-control"] == "no-store"
        cookie = exchanged.headers["set-cookie"]
        for attribute in (
            "HttpOnly",
            "Secure",
            "SameSite=strict",
            "Path=/api/client",
            "Max-Age=",
            "expires=",
        ):
            assert attribute in cookie
        assert "Domain=" not in cookie
        token = http.cookies.get(CLIENT_COOKIE)
        async with transaction(auth.engine) as db:
            row = await db.scalar(select(ClientSession).where(ClientSession.user_id == user["id"]))
            assert row.token_hash != token
        # A freshly mounted/reloaded portal reuses the browser cookie jar.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=http.base_url, cookies=http.cookies
        ) as refreshed:
            session = await refreshed.get("/api/client/session", headers=PORTAL)
            assert session.status_code == 200
            assert session.json() == {"expires_at": exchanged.json()["expires_at"]}
            assert session.headers["cache-control"] == "no-store"
            assert (await refreshed.get("/api/client/me", headers=PORTAL)).json()["id"] == str(
                user["id"]
            )
            device = await refreshed.post(
                "/api/client/devices", headers=PORTAL, json={"name": "Phone"}
            )
            assert device.status_code == 201
            admin = await refreshed.get("/api/auth/me")
            assert admin.status_code == 401 and "cookie" not in admin.request.headers
            assert (
                await refreshed.get("/api/auth/me", headers={"Cookie": CLIENT_COOKIE + "=" + token})
            ).status_code == 401
            logged_out = await refreshed.post("/api/client/logout", headers=PORTAL)
            assert logged_out.status_code == 204
            assert "Max-Age=0" in logged_out.headers["set-cookie"]
            assert (await refreshed.get("/api/client/session", headers=PORTAL)).status_code == 401
        # Even a saved copy of the cookie cannot revive the revoked DB session.
        assert (await http.get("/api/client/session", headers=PORTAL)).status_code == 401
        assert (
            "Max-Age=0"
            in (await http.get("/api/client/session", headers=PORTAL)).headers["set-cookie"]
        )


async def test_cookie_csrf_and_explicit_bearer_never_falls_back(cookie_app, auth):  # noqa: F811
    app, actor, user = cookie_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://portal.test"
    ) as http:
        invite = await app.state.client_auth.create_invitation(user["id"], 300, actor, "invite")
        body = {"token": invite["token"], "session_mode": "cookie"}
        assert (await http.post("/api/client/exchange", json=body)).status_code == 403
        for bad in (
            {"Origin": "https://evil.test"},
            {"Origin": "null"},
            {"Origin": "https://["},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ):
            assert (
                await http.post("/api/client/exchange", json=body, headers=PORTAL | bad)
            ).status_code == 403
        accepted = await http.post(
            "/api/client/exchange",
            json=body,
            headers=PORTAL | {"Origin": "https://portal.test", "Sec-Fetch-Site": "same-origin"},
        )
        assert accepted.status_code == 200
        assert (await http.post("/api/client/devices", json={"name": "CSRF"})).status_code == 403
        assert (
            await http.post(
                "/api/client/logout",
                headers=PORTAL | {"Origin": "https://evil.test", "X-Forwarded-Host": "evil.test"},
            )
        ).status_code == 403
        assert (await http.get("/api/client/session", headers=PORTAL)).status_code == 200
        preflight = await http.options(
            "/api/client/logout",
            headers={
                "Origin": "https://evil.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-TTCP-Client",
            },
        )
        assert "access-control-allow-origin" not in preflight.headers
        _, credentials, _ = await account(auth)
        admin = await login(http, credentials)
        assert (await http.get("/api/client/session", headers=admin | PORTAL)).status_code == 401
        assert (await http.get("/api/auth/me", headers=admin)).status_code == 200


@pytest.mark.parametrize("invalidate", ["session_expired", "user_expired", "user_disabled"])
async def test_cookie_session_fails_closed_on_current_state(cookie_app, auth, invalidate):  # noqa: F811
    app, actor, user = cookie_app
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://portal.test"
    ) as http:
        assert (await issue(http, app, actor, user)).status_code == 200
        async with transaction(auth.engine) as db:
            if invalidate == "session_expired":
                session = await db.scalar(
                    select(ClientSession).where(ClientSession.user_id == user["id"])
                )
                session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            else:
                current = await db.get(VpnUser, user["id"])
                if invalidate == "user_expired":
                    current.expires_at = datetime.now(UTC) - timedelta(seconds=1)
                else:
                    current.enabled = False
        denied = await http.get("/api/client/session", headers=PORTAL)
        assert denied.status_code == 401 and denied.headers["cache-control"] == "no-store"
        assert "Max-Age=0" in denied.headers["set-cookie"]
        assert (
            await http.post("/api/client/devices", headers=PORTAL, json={"name": "Denied"})
        ).status_code == 401
