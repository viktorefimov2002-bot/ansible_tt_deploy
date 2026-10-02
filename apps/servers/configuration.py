"""Closed, non-secret settings accepted by revision creation and node execution."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ServerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    ipv6_available: bool = Field(default=True, strict=True)
    allow_private_network_connections: bool = Field(default=False, strict=True)
    tls_handshake_timeout_secs: int = Field(default=10, strict=True, ge=1, le=120)
    client_listener_timeout_secs: int = Field(default=600, strict=True, ge=1, le=86400)
    connection_establishment_timeout_secs: int = Field(default=30, strict=True, ge=1, le=600)
    tcp_connections_timeout_secs: int = Field(default=604800, strict=True, ge=1, le=2592000)
    udp_connections_timeout_secs: int = Field(default=300, strict=True, ge=1, le=86400)


def validated_config_result(parameters, report):
    if not isinstance(report, dict) or set(report) != {"revision_id", "state", "active"}:
        raise ValueError("Invalid configuration apply report")
    revision, state, active = report["revision_id"], report["state"], report["active"]
    try:
        canonical = str(UUID(revision))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("Invalid configuration revision identity") from None
    if canonical != revision or revision != parameters.get("revision_id"):
        raise ValueError("Configuration revision does not match intent")
    if type(active) is not bool or state not in ("applied", "rolled_back", "rollback_failed"):
        raise ValueError("Invalid configuration apply state")
    if state in ("applied", "rolled_back") and not active:
        raise ValueError("Configuration health is not confirmed")
    return {"revision_id": revision, "state": state, "active": active}
