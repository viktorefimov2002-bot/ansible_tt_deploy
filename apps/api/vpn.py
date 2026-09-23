from uuid import UUID

from fastapi import APIRouter, Request

from apps.api.auth import Administrator, Authenticated
from apps.api.jobs import representation
from apps.vpn.schemas import AccessInput, DeviceInput, Enabled, UserInput, UserUpdate

router = APIRouter(prefix="/api/vpn-users")


@router.get("")
async def list_users(request: Request, principal: Authenticated):
    return await request.app.state.vpn.list_users()


@router.post("", status_code=201)
async def create_user(body: UserInput, request: Request, principal: Administrator):
    return await request.app.state.vpn.create_user(
        body, principal.admin_id, request.state.request_id
    )


@router.get("/{user_id}")
async def get_user(user_id: UUID, request: Request, principal: Authenticated):
    return await request.app.state.vpn.get_user(user_id)


@router.patch("/{user_id}")
async def update_user(user_id: UUID, body: UserUpdate, request: Request, principal: Administrator):
    return await request.app.state.vpn.update_user(
        user_id, body, principal.admin_id, request.state.request_id
    )


@router.put("/{user_id}/enabled")
async def enabled(user_id: UUID, body: Enabled, request: Request, principal: Administrator):
    return await request.app.state.vpn.set_enabled(
        user_id, body.enabled, principal.admin_id, request.state.request_id
    )


@router.put("/{user_id}/access")
async def access(user_id: UUID, body: AccessInput, request: Request, principal: Administrator):
    return await request.app.state.vpn.set_access(
        user_id, body, principal.admin_id, request.state.request_id
    )


@router.get("/{user_id}/devices")
async def list_devices(user_id: UUID, request: Request, principal: Authenticated):
    return await request.app.state.vpn.list_devices(user_id)


@router.post("/{user_id}/devices", status_code=201)
async def create_device(
    user_id: UUID, body: DeviceInput, request: Request, principal: Administrator
):
    return await request.app.state.vpn.create_device(
        user_id, body, principal.admin_id, request.state.request_id
    )


@router.delete("/{user_id}/devices/{device_id}")
async def revoke_device(user_id: UUID, device_id: UUID, request: Request, principal: Administrator):
    return await request.app.state.vpn.revoke_device(
        user_id, device_id, principal.admin_id, request.state.request_id
    )


@router.get("/{user_id}/devices/{device_id}/credentials")
async def list_credentials(
    user_id: UUID, device_id: UUID, request: Request, principal: Authenticated
):
    return await request.app.state.vpn.list_credentials(user_id, device_id)


@router.post("/{user_id}/devices/{device_id}/credentials/{server_id}", status_code=202)
async def create_credential(
    user_id: UUID, device_id: UUID, server_id: UUID, request: Request, principal: Administrator
):
    job = await request.app.state.vpn.create_credential(
        user_id, device_id, server_id, principal.admin_id, request.state.request_id
    )
    return representation(job)


@router.delete("/{user_id}/devices/{device_id}/credentials/{server_id}", status_code=202)
async def revoke_credential(
    user_id: UUID, device_id: UUID, server_id: UUID, request: Request, principal: Administrator
):
    job, credential = await request.app.state.vpn.revoke_credential(
        user_id, device_id, server_id, principal.admin_id, request.state.request_id
    )
    return representation(job) if job else credential


@router.post("/{user_id}/devices/{device_id}/credentials/{server_id}/download")
async def download(
    user_id: UUID, device_id: UUID, server_id: UUID, request: Request, principal: Administrator
):
    return await request.app.state.vpn.download(
        user_id, device_id, server_id, principal.admin_id, request.state.request_id
    )
