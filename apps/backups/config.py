"""Backup secrets are read only by the worker/maintenance process."""

import os
import stat
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class BackupFailure(Exception):
    def __init__(self, code="backup_failed", *, retryable=False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class PostgresConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    host: str = Field(pattern=r"^[A-Za-z0-9.:-]{1,253}$")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(pattern=r"^[A-Za-z0-9_]{1,63}$")
    user: str = Field(pattern=r"^[A-Za-z0-9_]{1,63}$")
    password: SecretStr = Field(min_length=1)
    sslmode: str = Field(default="require", pattern=r"^(disable|require|verify-full)$")

    def environment(self):
        # No inherited PGOPTIONS, service files, proxy variables or connection URIs.
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR") if k in os.environ}
        return env | {
            "PGHOST": self.host,
            "PGPORT": str(self.port),
            "PGDATABASE": self.database,
            "PGUSER": self.user,
            "PGPASSWORD": self.password.get_secret_value(),
            "PGSSLMODE": self.sslmode,
            "PGCONNECT_TIMEOUT": "10",
        }


class BackupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    endpoint: str
    region: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    bucket: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
    prefix: str = Field(default="ttcp", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    access_key: SecretStr = Field(min_length=1)
    secret_key: SecretStr = Field(min_length=1)
    session_token: SecretStr | None = None
    active_key_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    encryption_keys: dict[str, SecretStr] = Field(repr=False)
    postgres: PostgresConfig
    max_encrypted_bytes: int = Field(default=48 * 1024 * 1024, ge=1024, le=1024**3)
    max_plain_bytes: int = Field(default=1024**3, ge=1024, le=16 * 1024**3)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)

    @field_validator("endpoint")
    @classmethod
    def secure_endpoint(cls, value):
        p = urlsplit(value)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.path not in ("", "/")
            or p.query
            or p.fragment
        ):
            raise ValueError("HTTPS S3 endpoint required")
        return value.rstrip("/")

    @field_validator("encryption_keys")
    @classmethod
    def valid_keys(cls, value):
        import re

        if not value or len(value) > 20:
            raise ValueError("Keyring required")
        for name, secret in value.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
                raise ValueError("Invalid key identifier")
            if not re.fullmatch(r"[0-9a-fA-F]{64}", secret.get_secret_value()):
                raise ValueError("AES-256 keys must contain 64 hexadecimal characters")
        return value

    def key(self, key_id):
        try:
            return bytes.fromhex(self.encryption_keys[key_id].get_secret_value())
        except KeyError:
            raise BackupFailure("backup_unavailable") from None


def protected_config(path: str, model=BackupConfig):
    try:
        file = Path(path)
        info = file.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            raise ValueError()
        if os.name == "posix" and info.st_mode & 0o077:
            raise ValueError()
        config = model.model_validate_json(file.read_bytes())
        if isinstance(config, BackupConfig):
            config.key(config.active_key_id)
        return config
    except Exception:
        raise BackupFailure("backup_unavailable") from None
