"""Transport-independent, closed execution contract. Never persist these requests."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class Target(ClosedModel):
    # IP literals or DNS names only; no inventory patterns, URLs, options or templates.
    host: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}$")
    user: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int = Field(default=22, strict=True, ge=1, le=65535)
    host_key: str = Field(
        pattern=r"^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp256) [A-Za-z0-9+/]+={0,2}$",
        max_length=8192,
    )
    password: SecretStr | None = Field(default=None, repr=False, exclude=True)
    private_key: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def credentials(self):
        if (self.password is None) == (self.private_key is None):
            raise ValueError("Exactly one authentication method is required")
        for secret in (self.password, self.private_key):
            if secret is not None:
                value = secret.get_secret_value()
                # Ansible templates inventory values: never accept executable Jinja.
                if (
                    not value
                    or len(value) > 32768
                    or "\x00" in value
                    or any(marker in value for marker in ("{{", "{%", "{#"))
                ):
                    raise ValueError("Invalid authentication material")
        return self


class StatusParameters(ClosedModel):
    log_lines: int = Field(default=50, strict=True, ge=0, le=200)
    certificate_warning_days: int = Field(default=14, strict=True, ge=1, le=90)


class PreflightParameters(ClosedModel):
    domain: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")
    public_ip: str = Field(max_length=45)
    acme_http: bool = True


class CredentialParameters(ClosedModel):
    public_address: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.:-]{0,252}$")
    username: str = Field(pattern=r"^ttcp_[a-f0-9]{32}$", max_length=37)
    password: SecretStr | None = Field(default=None, repr=False, exclude=True)

    @model_validator(mode="after")
    def safe_secret(self):
        if self.password is not None:
            value = self.password.get_secret_value()
            if not 32 <= len(value) <= 128 or any(x in value for x in ("{{", "{%", "{#", "\x00")):
                raise ValueError("Invalid credential secret")
        return self


CHECKS = (
    "ssh",
    "dns_a",
    "dns_aaaa",
    "tcp_80",
    "tcp_443",
    "udp_443",
    "os",
    "architecture",
    "disk",
    "memory",
    "time_sync",
)
CHECK_STATES = ("pass", "fail", "unknown", "skipped")

LIFECYCLE = ("server.deploy", "server.restart", "server.update", "server.uninstall")
VERSION_PATTERN = r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"


class LifecycleParameters(ClosedModel):
    version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    domain: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9.-]{0,252}$")
    acme_http: bool = Field(default=False, strict=True)
    acme_email: str | None = Field(
        default=None, max_length=254, pattern=r"^[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+$"
    )


def preflight_ready(checks, acme_http):
    return (
        isinstance(checks, dict)
        and set(checks) == set(CHECKS)
        and any(checks[name] == "pass" for name in ("dns_a", "dns_aaaa"))
        and all(
            value == "pass"
            or (name in ("dns_aaaa", "dns_a") and value == "skipped")
            or (name == "tcp_80" and not acme_http and value == "skipped")
            for name, value in checks.items()
        )
    )


class ExecutionRequest(ClosedModel):
    operation: Literal[
        "server.status",
        "server.preflight",
        "execution.validate",
        "credential.create",
        "credential.revoke",
        "server.deploy",
        "server.restart",
        "server.update",
        "server.uninstall",
    ]
    target: Target | None = Field(default=None, repr=False, exclude=True)
    parameters: StatusParameters = Field(default_factory=StatusParameters)
    preflight: PreflightParameters | None = None
    credential: CredentialParameters | None = Field(default=None, repr=False, exclude=True)
    lifecycle: LifecycleParameters | None = None
    timeout_seconds: int = Field(default=45, strict=True, ge=1, le=600)

    @model_validator(mode="after")
    def selected_target(self):
        if self.operation not in LIFECYCLE and self.timeout_seconds > 45:
            raise ValueError("Only lifecycle operations admit extended deadlines")
        if self.operation in LIFECYCLE:
            p = self.lifecycle
            if p is None or (self.operation in ("server.deploy", "server.update")) != (
                p.version is not None
            ):
                raise ValueError("Lifecycle version is operation-specific")
            if self.operation == "server.deploy":
                if not p.domain or (p.acme_http and not p.acme_email):
                    raise ValueError("Deployment requires domain and ACME contact when applicable")
            elif p.domain is not None or p.acme_email is not None or p.acme_http:
                raise ValueError("Deployment parameters are operation-specific")
        elif self.lifecycle is not None:
            raise ValueError("Lifecycle parameters are operation-specific")
        if (self.operation != "execution.validate") != (self.target is not None):
            raise ValueError("Operation requires an explicit, appropriate target")
        if (self.operation == "server.preflight") != (self.preflight is not None):
            raise ValueError("Preflight parameters required only for preflight")
        if self.operation.startswith("credential."):
            if self.credential is None or (self.operation == "credential.create") != (
                self.credential.password is not None
            ):
                raise ValueError("Credential operation requires matching typed parameters")
        elif self.credential is not None:
            raise ValueError("Credential parameters are operation-specific")
        return self


class ExecutionEvent(StrEnum):
    STARTED = "execution_started"
    TASK_OK = "execution_task_ok"
    TASK_FAILED = "execution_task_failed"
    TASK_SKIPPED = "execution_task_skipped"
    UNREACHABLE = "execution_unreachable"
    OUTPUT_SUPPRESSED = "execution_output_suppressed"


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "execution_failed"
    UNREACHABLE = "execution_unreachable"
    TIMED_OUT = "execution_timeout"
    UNAVAILABLE = "execution_unavailable"
    INVALID = "execution_invalid"


@dataclass(frozen=True)
class ExecutionResult:
    outcome: Outcome
    exit_code: int | None = None
    checks: dict[str, str] | None = None
    configuration: dict[str, str] | None = field(default=None, repr=False)
    lifecycle: dict | None = None


EventSink = Callable[[ExecutionEvent], Awaitable[None]]


class ExecutionPort(Protocol):
    async def execute(self, request: ExecutionRequest, emit: EventSink) -> ExecutionResult:
        """Cancellation propagates only after local process cleanup. No raw output."""
        ...
