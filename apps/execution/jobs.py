"""Job orchestration depends on ExecutionPort, never on Ansible arguments/files."""

import asyncio
from typing import Protocol
from uuid import UUID

from apps.execution.ports import LIFECYCLE, ExecutionPort, ExecutionRequest, Outcome
from apps.jobs.service import LostClaim
from apps.jobs.worker import Context, ExecutionFailure, Handler, RetryableError


class RequestResolver(Protocol):
    async def resolve(
        self, target_id: UUID, operation: str = "server.status", parameters: dict | None = None
    ) -> ExecutionRequest:
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
            job.type in ("server.status", "server.preflight", "server.config.apply", *LIFECYCLE)
            and job.target_type == "server"
            and job.target_id is not None
            and resolver is not None
        ):
            request = (
                await resolver.resolve(job.target_id)
                if job.type == "server.status"
                else await resolver.resolve(job.target_id, job.type, job.parameters)
                if job.type in (*LIFECYCLE, "server.config.apply")
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
                try:
                    await context.service.execution_result(context.job_id, context.token, result)
                except ValueError:
                    raise ExecutionFailure("execution_invalid") from None
            if job.type == "server.preflight" and result.checks is not None:
                # A completed diagnostic may contain failed/unknown checks. Preserve the
                # report instead of turning prerequisite failures into worker failures.
                return
            if job.type == "server.config.apply" and result.config_apply is not None:
                from apps.servers.configuration import validated_config_result

                report = validated_config_result(job.parameters, result.config_apply)
                if report["state"] != "applied":
                    # A recovered failed attempt must remain failed; never replay
                    # the candidate automatically after a confirmed rollback.
                    raise ExecutionFailure("config_" + report["state"])
            if result.outcome != Outcome.SUCCEEDED:
                if job.type in (
                    "server.deploy",
                    "server.update",
                    "server.uninstall",
                    "server.config.apply",
                ) and result.outcome in (
                    Outcome.UNREACHABLE,
                    Outcome.TIMED_OUT,
                    Outcome.UNAVAILABLE,
                    Outcome.FAILED,
                ):
                    raise RetryableError
                raise ExecutionFailure(result.outcome.value)
            if job.type in LIFECYCLE and result.lifecycle is None:
                raise ExecutionFailure("execution_invalid")
            if job.type == "server.config.apply" and result.config_apply is None:
                raise ExecutionFailure("execution_invalid")
        finally:
            active.cancel()
            watcher.cancel()
            await asyncio.gather(active, watcher, return_exceptions=True)

    handlers = {"internal.execution.validate": Handler(execute, replay_safe=True, cancellable=True)}
    if resolver is not None:
        # Diagnostics can be interrupted; workload mutations must finish/reconcile.
        handlers["server.status"] = Handler(execute, replay_safe=True, cancellable=True)
        handlers["server.preflight"] = Handler(execute, replay_safe=True, cancellable=True)
        for operation in LIFECYCLE:
            handlers[operation] = Handler(
                execute,
                replay_safe=operation != "server.restart",
                cancellable=False,
                timeout_seconds=620,
            )
        handlers["server.config.apply"] = Handler(
            execute,
            replay_safe=True,
            cancellable=False,
            timeout_seconds=620,
        )
    return handlers
