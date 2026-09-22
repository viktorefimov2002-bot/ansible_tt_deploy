from uuid import UUID

from fastapi import APIRouter, Query, Request

from apps.api.auth import Administrator, Authenticated
from apps.api.jobs import representation
from apps.servers.schemas import CreateServer, Credentials, Enabled, JobInput, ServerInput

router = APIRouter(prefix="/api/servers")


@router.get("")
async def list_servers(
    request: Request,
    principal: Authenticated,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
):
    return await request.app.state.servers.list(offset, limit)


@router.get("/{server_id}")
async def get_server(server_id: UUID, request: Request, principal: Authenticated):
    return await request.app.state.servers.get(server_id)


@router.post("", status_code=201)
async def create_server(body: CreateServer, request: Request, principal: Administrator):
    return await request.app.state.servers.create(
        body, principal.admin_id, request.state.request_id
    )


@router.put("/{server_id}")
async def update_server(
    server_id: UUID, body: ServerInput, request: Request, principal: Administrator
):
    return await request.app.state.servers.update(
        server_id, body, principal.admin_id, request.state.request_id
    )


@router.put("/{server_id}/enabled")
async def enable_server(server_id: UUID, body: Enabled, request: Request, principal: Administrator):
    return await request.app.state.servers.update(
        server_id, body, principal.admin_id, request.state.request_id, "server.enabled"
    )


@router.delete("/{server_id}")
async def retire_server(server_id: UUID, request: Request, principal: Administrator):
    # Logical deletion retains references and audit; no remote uninstall is implied.
    return await request.app.state.servers.update(
        server_id,
        Enabled(enabled=False),
        principal.admin_id,
        request.state.request_id,
        "server.disable",
    )


@router.put("/{server_id}/credentials")
async def rotate_credentials(
    server_id: UUID, body: Credentials, request: Request, principal: Administrator
):
    return await request.app.state.servers.update(
        server_id, body, principal.admin_id, request.state.request_id, "server.credentials.rotate"
    )


@router.post("/{server_id}/preflight", status_code=202)
async def preflight(server_id: UUID, body: JobInput, request: Request, principal: Administrator):
    return representation(
        await request.app.state.servers.enqueue(
            server_id,
            "server.preflight",
            body.idempotency_key,
            principal.admin_id,
            request.state.request_id,
        )
    )


@router.post("/{server_id}/status", status_code=202)
async def status(server_id: UUID, body: JobInput, request: Request, principal: Administrator):
    return representation(
        await request.app.state.servers.enqueue(
            server_id,
            "server.status",
            body.idempotency_key,
            principal.admin_id,
            request.state.request_id,
        )
    )
