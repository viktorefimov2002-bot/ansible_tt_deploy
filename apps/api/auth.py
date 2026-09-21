"""Reusable request context and server-side administrative authorization."""

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.api.auth_service import AuthService, Principal

router = APIRouter(prefix="/api/auth")


def auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(503, "Authentication unavailable")
    return service


Service = Annotated[AuthService, Depends(auth_service)]


async def current_principal(request: Request, service: Service) -> Principal:
    authorization = request.headers.get("Authorization", "")
    match = re.fullmatch(r"(?i:Bearer) ([A-Za-z0-9_-]{43})", authorization)
    principal = await service.authenticate(match[1]) if match else None
    if principal is None:
        await service.denied(None, request.state.request_id)
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = principal
    return principal


Authenticated = Annotated[Principal, Depends(current_principal)]


async def require_admin(request: Request, principal: Authenticated, service: Service) -> Principal:
    if principal.role != "admin":
        await service.denied(principal, request.state.request_id)
        raise HTTPException(403, "Administrator role required")
    return principal


Administrator = Annotated[Principal, Depends(require_admin)]


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=1, max_length=256)
    totp_code: str = Field(pattern=r"^[0-9]{6}$", repr=False)


@router.post("/login")
async def login(body: Login, request: Request, service: Service):
    result = await service.login(
        body.username, body.password.get_secret_value(), body.totp_code, request.state.request_id
    )
    if result is None:
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    token, expires = result
    return {"access_token": token, "token_type": "bearer", "expires_at": expires}


@router.get("/me")
async def me(principal: Authenticated):
    return {
        "id": principal.admin_id,
        "username": principal.username,
        "role": principal.role,
        "session_id": principal.session_id,
    }


@router.post("/logout", status_code=204)
async def logout(request: Request, principal: Authenticated, service: Service):
    # Both roles may end their own authentication session.
    await service.revoke(principal, principal.session_id, request.state.request_id)
    return Response(status_code=204)


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: UUID, request: Request, principal: Administrator, service: Service):
    if not await service.revoke(principal, session_id, request.state.request_id):
        raise HTTPException(404, "Session not found")
    return Response(status_code=204)
