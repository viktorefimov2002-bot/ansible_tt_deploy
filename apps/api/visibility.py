"""Narrow authenticated audit and notification APIs for Admin Web."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, StrictBool

from apps.api.auth import Administrator, Authenticated
from apps.visibility.service import VisibilityService

router = APIRouter(prefix="/api")
FILTER_PATTERN = r"^[a-zA-Z0-9_.:-]+$"


def service(request: Request) -> VisibilityService:
    result = getattr(request.app.state, "visibility", None)
    if result is None:
        raise HTTPException(503, "Operational visibility unavailable")
    return result


@router.get("/audit")
async def audit(
    request: Request,
    principal: Authenticated,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    action: str | None = Query(None, min_length=1, max_length=128, pattern=FILTER_PATTERN),
    actor_type: Literal["admin", "vpn_user", "system"] | None = None,
    actor_id: UUID | None = None,
    target_type: str | None = Query(None, min_length=1, max_length=64, pattern=FILTER_PATTERN),
    target_id: UUID | None = None,
    result: str | None = Query(None, min_length=1, max_length=32, pattern=FILTER_PATTERN),
    request_id: str | None = Query(None, min_length=1, max_length=128, pattern=FILTER_PATTERN),
    since: datetime | None = None,
    until: datetime | None = None,
):
    if any(value is not None and value.tzinfo is None for value in (since, until)):
        raise HTTPException(422, "Audit timestamps must include a timezone")
    if since is not None and until is not None and since > until:
        raise HTTPException(422, "Audit time range is invalid")
    return await service(request).audit(
        offset=offset,
        limit=limit,
        action=action,
        actor_type=actor_type,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        result=result,
        request_id=request_id,
        since=since,
        until=until,
    )


@router.get("/notifications")
async def notifications(
    request: Request,
    principal: Authenticated,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
    unread_only: bool = False,
):
    return await service(request).notifications(
        principal.admin_id, offset=offset, limit=limit, unread_only=unread_only
    )


class ReadState(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    read: StrictBool


@router.put("/notifications/{event_id}/read")
async def set_read(
    event_id: UUID,
    body: ReadState,
    request: Request,
    principal: Administrator,
):
    notification = await service(request).set_read(event_id, principal.admin_id, body.read)
    if notification is None:
        raise HTTPException(404, "Notification not found")
    return notification
