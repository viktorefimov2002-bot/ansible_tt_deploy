"""Authenticated history and a fixed backup action. Restore has no HTTP route."""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from apps.api.auth import Administrator, Authenticated
from apps.api.jobs import representation
from apps.backups.config import BackupFailure
from apps.backups.service import BackupBusy, BackupService

router = APIRouter(prefix="/api/backups")


def service(request: Request) -> BackupService:
    result = getattr(request.app.state, "backups", None)
    if result is None:
        raise HTTPException(503, "Backups unavailable")
    return result


class RunBackup(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    idempotency_key: UUID


@router.get("")
async def history(
    request: Request,
    principal: Authenticated,
    offset: int = Query(0, ge=0, le=100000),
    limit: int = Query(50, ge=1, le=100),
):
    return await service(request).history(offset, limit)


@router.post("/run", status_code=202)
async def run(body: RunBackup, request: Request, principal: Administrator):
    try:
        job = await service(request).request(
            principal.admin_id, request.state.request_id, body.idempotency_key
        )
    except BackupFailure:
        raise HTTPException(503, "Backups unavailable") from None
    except BackupBusy:
        raise HTTPException(
            409, "Backup already active or request key belongs to another operator"
        ) from None
    return representation(job)
