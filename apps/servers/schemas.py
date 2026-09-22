import ipaddress
import re

from cryptography.hazmat.primitives.serialization import load_ssh_private_key, load_ssh_public_key
from pydantic import Field, SecretStr, field_validator

from apps.execution.ports import ClosedModel


def hostname(value: str) -> str:
    value = value.lower().rstrip(".")
    if "%" in value:
        raise ValueError("Scoped addresses are not supported")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        if len(value) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in value.split(".")
        ):
            raise ValueError("Invalid hostname") from None
        return value


class ServerInput(ClosedModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    hostname: str
    public_ip: str
    domain: str
    location: str | None = Field(default=None, max_length=255)
    ssh_user: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    ssh_port: int = Field(default=22, strict=True, ge=1, le=65535)
    acme_http: bool = Field(default=True, strict=True)

    _host = field_validator("hostname", "domain")(hostname)

    @field_validator("domain")
    @classmethod
    def dns_name(cls, value):
        if ":" in value or value.replace(".", "").isdigit():
            raise ValueError("A DNS name is required")
        return value

    @field_validator("public_ip")
    @classmethod
    def address(cls, value):
        if "%" in value:
            raise ValueError("Scoped addresses are not supported")
        return str(ipaddress.ip_address(value))

    @field_validator("ssh_user")
    @classmethod
    def management_user(cls, value):
        if value == "root":
            raise ValueError("Use a dedicated management user")
        return value


class Credentials(ClosedModel):
    host_key: str
    private_key: SecretStr = Field(repr=False, exclude=True)

    @field_validator("host_key")
    @classmethod
    def public_key(cls, value):
        if len(value) > 8192 or not re.fullmatch(
            r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/]+={0,2}", value
        ):
            raise ValueError("Invalid host key")
        load_ssh_public_key(value.encode("ascii"))
        return value

    @field_validator("private_key")
    @classmethod
    def private_material(cls, value):
        raw = value.get_secret_value()
        if len(raw) > 32768 or any(x in raw for x in ("{{", "{%", "{#", "\x00")):
            raise ValueError("Invalid private key")
        try:
            load_ssh_private_key(raw.encode("ascii"), password=None)
        except (TypeError, ValueError):
            raise ValueError("Invalid unencrypted OpenSSH private key") from None
        return value


class CreateServer(ServerInput):
    credentials: Credentials = Field(repr=False, exclude=True)


class Enabled(ClosedModel):
    enabled: bool = Field(strict=True)


class JobInput(ClosedModel):
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
