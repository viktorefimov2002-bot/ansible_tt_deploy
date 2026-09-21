"""Real password/MFA/session/RBAC checks, using disposable PostgreSQL schemas."""

import asyncio
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pyotp
import pytest
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import undefer

from apps.api.auth_service import AuthService, matching_step, token_digest
from apps.api.main import create_app
from apps.api.passwords import hash_password, verify_password
from apps.persistence.database import transaction
from apps.persistence.models import Admin, AdminSession, AuditEvent
from tests.test_migrations import database, migrate  # noqa: F401


@pytest.fixture
def key():
    return Fernet.generate_key().decode()


@pytest.fixture
async def auth(database, key):  # noqa: F811 - imported pytest fixture
    return AuthService(database, key, 300)


@pytest.fixture
async def client(auth):
    app = create_app()
    app.state.auth = auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        yield client


async def account(auth, role="admin", **overrides):
    password = secrets.token_urlsafe(24)
    seed = pyotp.random_base32()
    values = dict(
        username="test-" + uuid4().hex,
        role=role,
        enabled=True,
        password_hash=hash_password(password),
        totp_ciphertext=auth.cipher.encrypt(seed.encode()),
    )
    values.update(overrides)
    async with transaction(auth.engine) as db:
        record = Admin(**values)
        db.add(record)
        await db.flush()
        return (
            record.id,
            {
                "username": record.username,
                "password": password,
                "totp_code": pyotp.TOTP(seed).now(),
            },
            seed,
        )


async def login(client, body):
    response = await client.post("/api/auth/login", json=body)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def test_passwords_are_salted_argon2id_and_fail_closed():
    password = secrets.token_urlsafe(24)
    first, second = hash_password(password), hash_password(password)
    assert first.startswith("$argon2id$") and first != second
    assert verify_password(password, first)
    assert not verify_password("wrong", first)
    assert not verify_password(password, None)
    assert not verify_password(password, "invalid")
    for invalid in ("short", "x" * 257):
        with pytest.raises(ValueError):
            hash_password(invalid)


def test_totp_rfc6238_vector_window_and_replay():
    # RFC 6238 SHA1 vector at time 59, truncated to six digits.
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    at = datetime.fromtimestamp(59, UTC)
    assert matching_step(seed, "287082", at, None) == 1
    assert matching_step(seed, "287082", at, 1) is None
    assert matching_step(seed, "287082", at + timedelta(seconds=30), None) == 1
    assert matching_step(seed, "287082", at + timedelta(seconds=60), None) is None
    assert matching_step(seed, "000000", at, None) is None


def test_key_validation_redacts_input(settings, key):
    configured = type(settings)(**{**settings.model_dump(), "auth_encryption_key": key})
    assert key not in repr(configured)
    with pytest.raises(ValidationError) as error:
        type(settings)(
            **{**settings.model_dump(), "auth_encryption_key": "invalid-key-do-not-echo"}
        )
    assert "invalid-key-do-not-echo" not in str(error.value)


async def test_missing_key_fails_closed_without_breaking_health():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/api/auth/me")).status_code == 503


@pytest.mark.parametrize("authorization", ["", "Basic abc", "Bearer invalid", "Bearer " + "x" * 43])
async def test_unauthenticated_requests_rejected(client, authorization):
    for method, path in (
        ("GET", "/api/auth/me"),
        ("POST", "/api/auth/logout"),
        ("DELETE", f"/api/auth/sessions/{uuid4()}"),
    ):
        response = await client.request(method, path, headers={"Authorization": authorization})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
    # Cookies/query strings never become authentication credentials.
    assert (
        await client.get("/api/auth/me?access_token=ignored", headers={"Cookie": "token=ignored"})
    ).status_code == 401


async def test_admin_viewer_permissions_and_revocation(client, auth):
    _, admin_body, _ = await account(auth)
    _, viewer_body, _ = await account(auth, "viewer")
    admin_headers = await login(client, admin_body)
    viewer_headers = await login(client, viewer_body)
    viewer = await client.get("/api/auth/me", headers=viewer_headers)
    assert viewer.status_code == 200 and viewer.json()["role"] == "viewer"
    assert set(viewer.json()) == {"id", "username", "role", "session_id"}
    admin = await client.get("/api/auth/me", headers=admin_headers)
    assert admin.json()["role"] == "admin"
    target = "/api/auth/sessions/" + viewer.json()["session_id"]
    assert (await client.delete(target, headers=viewer_headers)).status_code == 403
    assert (await client.get("/api/auth/me", headers=viewer_headers)).status_code == 200
    assert (await client.delete(target, headers=admin_headers)).status_code == 204
    assert (await client.delete(target, headers=admin_headers)).status_code == 204
    assert (await client.get("/api/auth/me", headers=viewer_headers)).status_code == 401
    assert (
        await client.delete(f"/api/auth/sessions/{uuid4()}", headers=admin_headers)
    ).status_code == 404


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_logout_expiry_and_live_identity_checks(client, auth, role):
    identity, body, _ = await account(auth, role)
    headers = await login(client, body)
    token = headers["Authorization"].split()[1]
    # Independent service instances use the same durable sessions.
    assert await AuthService(auth.engine, Fernet.generate_key().decode(), 300).authenticate(token)
    async with transaction(auth.engine) as db:
        row = await db.scalar(select(AdminSession))
        assert row.token_hash == token_digest(token) and token not in row.token_hash
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    async with transaction(auth.engine) as db:
        row = await db.scalar(select(AdminSession))
        row.expires_at = datetime.now(UTC) + timedelta(minutes=5)
        record = await db.get(Admin, identity)
        record.enabled = False
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    async with transaction(auth.engine) as db:
        record = await db.get(Admin, identity)
        record.enabled = True
        record.role = "viewer"
    assert (
        await client.delete(f"/api/auth/sessions/{uuid4()}", headers=headers)
    ).status_code == 403
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_invalid_login_uniform_audited_and_no_secret_echo(client, auth, caplog):
    _, body, seed = await account(auth)
    cases = [
        {**body, "password": "wrong"},
        {**body, "totp_code": "000000"},
        {**body, "username": "missing"},
    ]
    # Avoid the tiny probability that the randomly generated valid code is 000000.
    cases[1]["totp_code"] = "111111" if body["totp_code"] == "000000" else "000000"
    for value in cases:
        response = await client.post("/api/auth/login", json=value)
        assert response.status_code == 401 and response.json() == {"detail": "Invalid credentials"}
    response = await client.post("/api/auth/login", json={**body, "password": {"secret": seed}})
    assert response.status_code == 422 and seed not in response.text
    response = await client.post("/api/auth/login", json={**body, "role": "admin"})
    assert response.status_code == 422
    async with transaction(auth.engine) as db:
        events = list(await db.scalars(select(AuditEvent)))
        assert len(events) == 3 and all(e.result == "denied" and e.request_id for e in events)
        assert list(await db.scalars(select(AdminSession))) == []
        rendered = json.dumps([e.details for e in events]) + caplog.text
        assert body["password"] not in rendered and seed not in rendered


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled": False},
        {"password_hash": None},
        {"totp_ciphertext": None},
        {"totp_ciphertext": b"invalid"},
    ],
)
async def test_disabled_unenrolled_or_corrupt_identity_cannot_login(client, auth, overrides):
    _, body, _ = await account(auth, **overrides)
    assert (await client.post("/api/auth/login", json=body)).status_code == 401


async def test_lockout_and_recovery(client, auth):
    identity, body, _ = await account(auth)
    for _ in range(5):
        assert (
            await client.post("/api/auth/login", json={**body, "password": "wrong"})
        ).status_code == 401
    assert (await client.post("/api/auth/login", json=body)).status_code == 401
    async with transaction(auth.engine) as db:
        record = await db.get(Admin, identity)
        assert record.locked_until is not None
        record.locked_until = datetime.now(UTC) - timedelta(seconds=1)
    await login(client, body)


async def test_concurrent_totp_replay_only_issues_one_session(client, auth):
    _, body, _ = await account(auth)
    responses = await asyncio.gather(*(client.post("/api/auth/login", json=body) for _ in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 401]
    async with transaction(auth.engine) as db:
        assert len(list(await db.scalars(select(AdminSession)))) == 1


async def test_bootstrap_atomic_concurrent_refusal_and_encryption(auth):
    seed, password = pyotp.random_base32(), secrets.token_urlsafe(24)
    results = await asyncio.gather(
        *(auth.bootstrap(name, password, seed, pyotp.TOTP(seed).now()) for name in ("one", "two")),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    async with transaction(auth.engine) as db:
        record = await db.scalar(select(Admin).options(undefer(Admin.totp_ciphertext)))
        assert record.role == "admin" and record.enabled
        assert seed.encode() not in record.totp_ciphertext
        assert auth.cipher.decrypt(record.totp_ciphertext).decode() == seed
        assert verify_password(password, record.password_hash)
        assert len(list(await db.scalars(select(AuditEvent)))) == 1
    assert await auth.login(record.username, password, pyotp.TOTP(seed).now(), "test") is None


async def test_upgrade_preserves_ttcp005_identity_and_downgrade(auth):
    await migrate(auth.engine, "0002_guards", downgrade=True)
    async with auth.engine.begin() as connection:
        await connection.execute(text("INSERT INTO admins(username) VALUES ('legacy')"))
    await migrate(auth.engine)
    async with transaction(auth.engine) as db:
        record = await db.scalar(select(Admin))
        assert record.username == "legacy" and not record.enabled and record.failed_logins == 0
    await migrate(auth.engine, "0002_guards", downgrade=True)
    await migrate(auth.engine)


async def test_database_failure_redacted(client, auth, monkeypatch):
    from sqlalchemy.exc import OperationalError

    async def broken(*args):
        raise OperationalError("secret-sql", {}, Exception("secret-password"))

    monkeypatch.setattr(auth, "authenticate", broken)
    response = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + "a" * 43})
    assert response.status_code == 503 and "secret" not in response.text


async def test_audit_failure_rolls_back_session_and_totp(client, auth, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    identity, body, _ = await account(auth)

    def broken_audit(*args):
        raise IntegrityError("private-sql", {}, Exception("private-error"))

    monkeypatch.setattr("apps.api.auth_service.audit", broken_audit)
    response = await client.post("/api/auth/login", json=body)
    assert response.status_code == 503 and "private" not in response.text
    async with transaction(auth.engine) as db:
        assert list(await db.scalars(select(AdminSession))) == []
        assert (await db.get(Admin, identity)).totp_last_step is None


async def test_failed_bootstrap_creates_nothing(auth):
    seed, password = pyotp.random_base32(), secrets.token_urlsafe(24)
    wrong = "000000" if pyotp.TOTP(seed).now() != "000000" else "111111"
    # Use malformed code to ensure it cannot match an adjacent valid time step.
    with pytest.raises(ValueError):
        await auth.bootstrap("initial", password, seed, wrong + "0")
    async with transaction(auth.engine) as db:
        assert list(await db.scalars(select(Admin))) == []
        assert list(await db.scalars(select(AuditEvent))) == []
