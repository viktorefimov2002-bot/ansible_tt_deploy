"""Real durable worker/Redis with a closed, non-networked managed-node CI fixture."""

import asyncio
import base64
import json
import os
import signal

from apps.execution.ports import ExecutionResult, Outcome
from apps.jobs.redis import RedisTransport
from apps.jobs.service import JobService
from apps.jobs.worker import HANDLERS, Worker
from apps.shared.config import load_settings
from apps.shared.dependencies import connected_dependencies
from apps.vpn.jobs import credential_handlers
from apps.vpn.service import VpnService


class DisposableCredentialPort:
    async def execute(self, request, emit):
        if (
            request.operation not in ("credential.create", "credential.revoke")
            or request.target.host != "managed-node.invalid"
            or request.target.user != "ci_manager"
            or request.credential is None
        ):
            return ExecutionResult(Outcome.INVALID)
        if request.operation == "credential.revoke":
            return ExecutionResult(Outcome.SUCCEEDED)
        if request.credential.public_address != "vpn-node.invalid":
            return ExecutionResult(Outcome.INVALID)
        # Endpoint execution alone is synthetic. Domain handlers still encrypt,
        # persist, authorize, audit and revoke the configuration through PostgreSQL.
        configuration = (
            '# Disposable CI configuration; no VPN endpoint\nendpoint = "vpn-node.invalid"\n'
            f'username = "{request.credential.username}"\n'
            f'password = "{request.credential.password.get_secret_value()}"\n'
        )
        return ExecutionResult(
            Outcome.SUCCEEDED,
            configuration={
                "toml": configuration,
                "deep_link": "tt://"
                + base64.urlsafe_b64encode(json.dumps({"config": configuration}).encode()).decode(),
            },
        )


async def run():
    settings = load_settings()
    if (
        os.environ.get("TTCP_CI_DISPOSABLE") != "1"
        or settings.postgres_host != "postgres"
        or settings.postgres_db != "ttcp_e2e"
        or settings.postgres_user != "ttcp_e2e"
        or settings.redis_host != "redis"
        or settings.auth_encryption_key is None
    ):
        raise RuntimeError("Only the disposable CI topology is permitted")
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set)
    async with connected_dependencies(settings) as dependencies:
        transport = RedisTransport(dependencies.redis)
        jobs = JobService(dependencies.engine, transport, transport)
        vpn = VpnService(dependencies.engine, settings.auth_encryption_key.get_secret_value())
        await Worker(
            jobs, {**HANDLERS, **credential_handlers(DisposableCredentialPort(), vpn)}
        ).run(stop)


if __name__ == "__main__":
    asyncio.run(run())
