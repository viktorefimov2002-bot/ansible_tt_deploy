"""Client-only authentication and owned self-service API."""

import hashlib
import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.api.auth import Administrator
from apps.api.client_auth_service import ClientAuthService, ClientPrincipal
from apps.vpn.schemas import DeviceInput

admin_invites = APIRouter(prefix="/api/vpn-users/{user_id}/invitations")
router = APIRouter(prefix="/api/client")


class InvitationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lifetime_seconds: int = Field(default=86400, ge=300, le=604800)


class ExchangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    token: SecretStr = Field(min_length=43, max_length=43)


def service(request: Request) -> ClientAuthService:
    return request.app.state.client_auth


ClientService = Annotated[ClientAuthService, Depends(service)]


async def client_principal(request: Request, auth: ClientService) -> ClientPrincipal:
    value = request.headers.get("Authorization", "")
    match = re.fullmatch(r"(?i:Bearer) ([A-Za-z0-9_-]{43})", value)
    principal = await auth.authenticate(match[1]) if match else None
    if principal is None:
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    request.state.client_principal = principal
    return principal


Client = Annotated[ClientPrincipal, Depends(client_principal)]


@admin_invites.post("", status_code=201)
async def create_invitation(
    user_id: UUID,
    body: InvitationInput,
    request: Request,
    principal: Administrator,
    auth: ClientService,
):
    try:
        result = await auth.create_invitation(
            user_id, body.lifetime_seconds, principal.admin_id, request.state.request_id
        )
    except ValueError:
        raise HTTPException(409, "VPN user unavailable") from None
    if result is None:
        raise HTTPException(404, "VPN user not found")
    return result


@admin_invites.get("")
async def list_invitations(user_id: UUID, principal: Administrator, auth: ClientService):
    result = await auth.list_invitations(user_id)
    if result is None:
        raise HTTPException(404, "VPN user not found")
    return result


@admin_invites.get("/{invitation_id}")
async def get_invitation(
    user_id: UUID, invitation_id: UUID, principal: Administrator, auth: ClientService
):
    result = await auth.get_invitation(user_id, invitation_id)
    if result is None:
        raise HTTPException(404, "Invitation not found")
    return result


@admin_invites.delete("/{invitation_id}", status_code=204)
async def revoke_invitation(
    user_id: UUID,
    invitation_id: UUID,
    request: Request,
    principal: Administrator,
    auth: ClientService,
):
    if not await auth.revoke_invitation(
        user_id, invitation_id, principal.admin_id, request.state.request_id
    ):
        raise HTTPException(404, "Invitation not found")
    return Response(status_code=204)


@router.post("/exchange")
async def exchange(body: ExchangeInput, request: Request, auth: ClientService):
    # Redis is ephemeral coordination only; a failure closes this public endpoint.
    # Use the socket peer, never an untrusted forwarded header. The edge proxy may
    # enforce a finer per-client policy when configured with trusted real IPs.
    peer = request.client.host if request.client else "unknown"
    key = "ttcp:client:exchange:" + hashlib.sha256(peer.encode()).hexdigest()
    try:
        count = await request.app.state.dependencies.redis.eval(
            "local n=redis.call('INCR',KEYS[1]); "
            "if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n",
            1,
            key,
        )
    except Exception:
        raise HTTPException(503, "Authentication unavailable") from None
    if count > 30:
        raise HTTPException(429, "Too many requests")
    token = body.token.get_secret_value()
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(401, "Invalid invitation")
    result = await auth.exchange(token, request.state.request_id)
    if result is None:
        raise HTTPException(401, "Invalid invitation")
    session_token, expires_at = result
    return {"access_token": session_token, "token_type": "bearer", "expires_at": expires_at}


@router.post("/logout", status_code=204)
async def logout(request: Request, principal: Client, auth: ClientService):
    await auth.revoke_session(principal, request.state.request_id)
    return Response(status_code=204)


@router.get("/me")
async def me(request: Request, principal: Client):
    user = await request.app.state.vpn.get_user(principal.user_id)
    devices = await request.app.state.vpn.list_devices(principal.user_id)
    return {
        name: user[name] for name in ("id", "display_name", "device_limit", "enabled", "expires_at")
    } | {"devices_used": sum(device["enabled"] for device in devices)}


@router.get("/servers")
async def servers(request: Request, principal: Client):
    return await request.app.state.vpn.list_accessible_servers(principal.user_id)


@router.get("/devices")
async def devices(request: Request, principal: Client):
    return await request.app.state.vpn.list_devices(principal.user_id)


@router.post("/devices", status_code=201)
async def create_device(body: DeviceInput, request: Request, principal: Client):
    return await request.app.state.vpn.create_device(
        principal.user_id, body, None, request.state.request_id, client_actor=principal.user_id
    )


@router.delete("/devices/{device_id}")
async def revoke_device(device_id: UUID, request: Request, principal: Client):
    return await request.app.state.vpn.revoke_device(
        principal.user_id, device_id, None, request.state.request_id, client_actor=principal.user_id
    )


@router.get("/devices/{device_id}/credentials")
async def credentials(device_id: UUID, request: Request, principal: Client):
    return await request.app.state.vpn.list_credentials(principal.user_id, device_id)


@router.post("/devices/{device_id}/credentials/{server_id}", status_code=202)
async def create_credential(device_id: UUID, server_id: UUID, request: Request, principal: Client):
    job = await request.app.state.vpn.create_credential(
        principal.user_id,
        device_id,
        server_id,
        None,
        request.state.request_id,
        client_actor=principal.user_id,
    )
    # Job metadata is intentionally minimal; admin job history is not client-readable.
    return {"id": job.id, "status": job.status}
