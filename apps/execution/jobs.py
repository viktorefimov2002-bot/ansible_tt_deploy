"""Job orchestration depends on ExecutionPort, never on Ansible arguments/files."""

import asyncio
from typing import Protocol
from uuid import UUID

from apps.execution.ports import ExecutionPort, ExecutionRequest, Outcome
from apps.jobs.service import LostClaim
from apps.jobs.worker import Context, ExecutionFailure, Handler


class RequestResolver(Protocol):
    async def resolve(self, target_id: UUID, operation: str = "server.status") -> ExecutionRequest:
        """Load authorized durable intent and ephemeral secrets; never from job metadata."""
        ...


def execution_handlers(
    port: ExecutionPort, resolver: RequestResolver | None = None
) -> dict[str, Handler]:
    async def execute(context: Context):
        await context.service.check_execution(context.job_id, context.token)
        job = await context.service.get(context.job_id)
        if job is None:
            raise LostClaim
        if job.type == "internal.execution.validate" and job.target_type == "internal":
            if job.target_id is not None:
                raise ExecutionFailure("execution_invalid")
            request = ExecutionRequest(operation="execution.validate")
        elif (
            job.type in ("server.status", "server.preflight")
            and job.target_type == "server"
            and job.target_id is not None
            and resolver is not None
        ):
            request = (
                await resolver.resolve(job.target_id)
                if job.type == "server.status"
                else await resolver.resolve(job.target_id, job.type)
            )
            if request.operation != job.type:
                raise ExecutionFailure("execution_invalid")
        else:
            raise ExecutionFailure("execution_invalid")

        async def emit(event):
            await context.service.execution_event(context.job_id, context.token, event.value)

        async def watch():
            while True:
                await context.service.check_execution(context.job_id, context.token)
                await asyncio.sleep(0.25)

        active = asyncio.create_task(port.execute(request, emit))
        watcher = asyncio.create_task(watch())
        try:
            await asyncio.wait({active, watcher}, return_when=asyncio.FIRST_COMPLETED)
            # Claim loss/cancellation wins if it races with process completion.
            if watcher.done():
                await watcher
            result = await active
            await context.service.check_execution(context.job_id, context.token)
            if resolver is not None and job.target_type == "server":
                await context.service.execution_result(context.job_id, context.token, result)
            if job.type == "server.preflight" and result.checks is not None:
                # A completed diagnostic may contain failed/unknown checks. Preserve the
                # report instead of turning prerequisite failures into worker failures.
                return
            if result.outcome != Outcome.SUCCEEDED:
                raise ExecutionFailure(result.outcome.value)
        finally:
            active.cancel()
            watcher.cancel()
            await asyncio.gather(active, watcher, return_exceptions=True)

    handlers = {"internal.execution.validate": Handler(execute, replay_safe=True, cancellable=True)}
    if resolver is not None:
        # Read-only status is safe to interrupt/replay. Mutations are not admitted.
        handlers["server.status"] = Handler(execute, replay_safe=True, cancellable=True)
        handlers["server.preflight"] = Handler(execute, replay_safe=True, cancellable=True)
    return handlers
