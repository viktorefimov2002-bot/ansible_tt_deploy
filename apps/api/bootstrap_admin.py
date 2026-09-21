"""Offline, interactive first administrator enrollment; never accepts secret argv."""

import asyncio
import getpass
import sys

from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.auth_service import AuthService
from apps.persistence.database import database_url
from apps.shared.config import load_auth_settings


async def bootstrap(username, password, seed, code):
    settings = load_auth_settings()
    if settings.auth_encryption_key is None:
        raise ValueError("Set TTCP_AUTH_ENCRYPTION_KEY before enrollment")
    engine = create_async_engine(database_url(settings), hide_parameters=True)
    try:
        service = AuthService(
            engine, settings.auth_encryption_key.get_secret_value(), settings.auth_session_seconds
        )
        await service.bootstrap(username, password, seed, code)
    finally:
        await engine.dispose()


def main():
    if not sys.stdin.isatty():
        print("Interactive terminal required", file=sys.stderr)
        return 1
    try:
        username = input("Initial administrator username: ")
        password = getpass.getpass("Password (12–256 characters): ")
        if getpass.getpass("Confirm password: ") != password:
            print("Passwords do not match", file=sys.stderr)
            return 1
        seed = getpass.getpass("Authenticator seed (32 uppercase base32 characters): ")
        code = getpass.getpass("Current authenticator code: ")
        asyncio.run(bootstrap(username, password, seed, code))
    except (Exception, KeyboardInterrupt):
        # DB/validation exception text may contain secrets or bound parameters.
        print(
            "Enrollment failed; check configuration, input and existing administrator",
            file=sys.stderr,
        )
        return 1
    print("Initial administrator enrolled. Wait for the next authenticator code before login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
