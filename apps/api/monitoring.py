from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from apps.api.auth import Authenticated

router = APIRouter(prefix="/api/monitoring")


@router.get("")
async def monitoring(
    request: Request, principal: Authenticated, window: Literal["1h", "6h", "24h"] = "1h"
):
    service = getattr(request.app.state, "monitoring", None)
    if service is None:
        raise HTTPException(503, "Monitoring unavailable")
    return await service.summary(window)
