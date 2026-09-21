"""Bounded Argon2id verification, with equal hash work for missing credentials."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
_dummy_hash = hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must contain 12 to 256 characters")
    return hasher.hash(password)


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        valid = hasher.verify(encoded or _dummy_hash, password)
        return valid and encoded is not None
    except (VerificationError, InvalidHashError):
        return False
