"""Reusable request context and server-side administrative authorization."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.api.auth_service import AuthService, Principal
from apps.api.browser import require_browser_request

router = APIRouter(prefix="/api/auth")
ADMIN_COOKIE = "__Host-ttcp_admin"
ADMIN_COOKIE_PATH = "/"


def clear_admin_cookie(response: Response):
    response.delete_cookie(
        ADMIN_COOKIE, path=ADMIN_COOKIE_PATH, secure=True, httponly=True, samesite="strict"
    )


def auth_service(request: Request) -> AuthService:
    service = getattr(request.app.state, "auth", None)
    if service is None:
        raise HTTPException(503, "Authentication unavailable")
    return service


Service = Annotated[AuthService, Depends(auth_service)]


async def current_principal(request: Request, service: Service) -> Principal:
    if "Authorization" in request.headers:
        match = re.fullmatch(r"(?i:Bearer) ([A-Za-z0-9_-]{43})", request.headers["Authorization"])
        token = match[1] if match else None
    else:
        token = request.cookies.get(ADMIN_COOKIE)
        if token:
            require_browser_request(request, "X-TTCP-Admin", "web")
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
                token = None
    principal = await service.authenticate(token) if token else None
    if principal is None:
        await service.denied(None, request.state.request_id)
        raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})
    request.state.principal = principal
    request.state.auth_token = token
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
    session_mode: Literal["bearer", "cookie"] = "bearer"


@router.post("/login")
async def login(body: Login, request: Request, response: Response, service: Service):
    if body.session_mode == "cookie":
        require_browser_request(request, "X-TTCP-Admin", "web")
    result = await service.login(
        body.username, body.password.get_secret_value(), body.totp_code, request.state.request_id
    )
    if result is None:
        raise HTTPException(401, "Invalid credentials", headers={"WWW-Authenticate": "Bearer"})
    token, expires = result
    if body.session_mode == "cookie":
        response.set_cookie(
            ADMIN_COOKIE,
            token,
            path=ADMIN_COOKIE_PATH,
            secure=True,
            httponly=True,
            samesite="strict",
            expires=expires,
            max_age=max(0, int((expires - datetime.now(UTC)).total_seconds())),
        )
        return {"token_type": "cookie", "expires_at": expires}
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
    response = Response(status_code=204)
    clear_admin_cookie(response)
    return response


@router.delete("/sessions/{session_id}", status_code=204)
async def revoke(session_id: UUID, request: Request, principal: Administrator, service: Service):
    if not await service.revoke(principal, session_id, request.state.request_id):
        raise HTTPException(404, "Session not found")
    return Response(status_code=204)
