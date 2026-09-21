import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from apps.api.auth import Administrator, Authenticated
from apps.jobs.ports import TransportUnavailable
from apps.jobs.service import TERMINAL, CancellationUnsafe, JobService

router = APIRouter(prefix="/api/jobs")


def service(request: Request) -> JobService:
    return request.app.state.jobs


def representation(job):
    return {
        name: getattr(job, name)
        for name in (
            "id",
            "type",
            "target_type",
            "target_id",
            "status",
            "progress",
            "created_by",
            "created_at",
            "started_at",
            "finished_at",
            "error_code",
            "error_message",
            "attempts",
            "max_attempts",
            "cancel_requested_at",
            "cancellable",
            "history",
            "log_sequence",
            "request_id",
        )
    }


@router.get("/{job_id}")
async def inspect_job(job_id: UUID, request: Request, principal: Authenticated):
    job = await service(request).get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return representation(job)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: UUID, request: Request, principal: Administrator):
    try:
        job = await service(request).cancel(job_id, principal.admin_id, request.state.request_id)
    except CancellationUnsafe:
        raise HTTPException(409, "Job cannot be cancelled safely") from None
    if job is None:
        raise HTTPException(404, "Job not found")
    return representation(job)


def frame(event: str, data: dict, sequence: int | None = None) -> str:
    prefix = f"id: {sequence}\n" if sequence is not None else ""
    return f"{prefix}event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/{job_id}/logs")
async def stream_logs(job_id: UUID, request: Request, principal: Authenticated):
    jobs = service(request)
    initial = await jobs.get(job_id)
    if initial is None:
        raise HTTPException(404, "Job not found")
    raw_cursor = request.headers.get("Last-Event-ID", "0")
    if not raw_cursor.isascii() or not raw_cursor.isdecimal() or len(raw_cursor) > 12:
        raise HTTPException(422, "Invalid log cursor")
    cursor = int(raw_cursor)
    if cursor > initial.log_sequence:
        raise HTTPException(422, "Log cursor is ahead of job history")
    token = request.headers["Authorization"].split(" ", 1)[1]

    async def stream():
        after = cursor
        while not await request.is_disconnected():
            # Recheck expiry/revocation/disabled accounts while the stream is open.
            if await request.app.state.auth.authenticate(token) is None:
                yield frame("auth_expired", {})
                return
            try:
                live = await jobs.events.read(job_id, after)
            except TransportUnavailable:
                live = []
            job = await jobs.get(job_id)
            if job is None:
                return
            merged = {e["sequence"]: e for e in live + job.history if e["sequence"] > after}
            for sequence, event in sorted(merged.items()):
                if sequence > after + 1:
                    yield frame("gap", {"after": after, "next": sequence})
                yield frame("log", event, sequence)
                after = sequence
            if job.status in TERMINAL:
                yield frame("done", {"status": job.status})
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
