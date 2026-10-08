"""Seed only the isolated CI database; generate fresh disposable browser identities."""

import asyncio
import json
import os
import secrets
from pathlib import Path

import pyotp
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.passwords import hash_password
from apps.persistence.database import database_url, transaction
from apps.persistence.models import Admin
from apps.shared.config import load_settings


def write_fixture(identities):
    path = Path("/fixture/browser.json")
    path.write_text(json.dumps(identities), encoding="utf-8")
    path.chmod(0o600)


async def seed():
    settings = load_settings()
    if (
        settings.postgres_host != "postgres"
        or settings.postgres_db != "ttcp_e2e"
        or settings.postgres_user != "ttcp_e2e"
        or settings.redis_host != "redis"
        or os.environ.get("TTCP_CI_DISPOSABLE") != "1"
        or settings.auth_encryption_key is None
    ):
        raise RuntimeError("Only the disposable CI topology is permitted")
    cipher = Fernet(settings.auth_encryption_key.get_secret_value().encode())
    identities = {}
    engine = create_async_engine(database_url(settings), hide_parameters=True)
    try:
        async with transaction(engine) as db:
            if await db.scalar(select(func.count()).select_from(Admin)):
                raise RuntimeError("CI database must be empty before seeding")
            for role in ("admin", "viewer"):
                password, totp_seed = secrets.token_urlsafe(24), pyotp.random_base32()
                username = "ci-" + role
                db.add(
                    Admin(
                        username=username,
                        role=role,
                        enabled=True,
                        password_hash=hash_password(password),
                        totp_ciphertext=cipher.encrypt(totp_seed.encode()),
                    )
                )
                identities[role] = {
                    "username": username,
                    "password": password,
                    "totp_seed": totp_seed,
                }
        identity = Ed25519PrivateKey.generate()
        identities["ssh"] = {
            "private_key": identity.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.OpenSSH,
                serialization.NoEncryption(),
            ).decode(),
            "host_key": identity.public_key()
            .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
            .decode(),
        }
        await asyncio.to_thread(write_fixture, identities)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
