import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from apps.jobs.ports import TransportUnavailable
from apps.jobs.service import EXECUTION_FAILURES, CancellationRequested, JobService, LostClaim


class RetryableError(Exception):
    """A handler explicitly classifies a failure; exception text is never persisted."""


class ExecutionFailure(Exception):
    def __init__(self, code: str):
        self.code = code if code in EXECUTION_FAILURES else "handler_failed"
        super().__init__(self.code)


@dataclass(frozen=True)
class Context:
    service: JobService
    job_id: UUID
    token: UUID
    attempt: int

    async def checkpoint(self, progress: int):
        await self.service.checkpoint(self.job_id, self.token, progress)


@dataclass(frozen=True)
class Handler:
    execute: Callable[[Context], Awaitable[None]]
    replay_safe: bool = False
    cancellable: bool = False


async def noop(context: Context):
    await context.checkpoint(50)


HANDLERS = {"internal.noop": Handler(noop, replay_safe=True, cancellable=True)}


class Worker:
    def __init__(
        self,
        service: JobService,
        handlers: dict[str, Handler] | None = None,
        *,
        timeout: float = 60,
        grace: float = 20,
        maintenance=None,
    ):
        self.service = service
        self.handlers = HANDLERS if handlers is None else handlers
        self.timeout, self.grace = timeout, grace
        self.maintenance = maintenance

    async def execute(self, job_id: UUID):
        job = await self.service.claim(job_id, lease_seconds=self.timeout + 30)
        if job is None:
            return
        token = job.claim_token
        assert token is not None
        handler = self.handlers.get(job.type)
        code, retryable = None, False
        try:
            if (
                handler is None
                or handler.replay_safe != job.replay_safe
                or handler.cancellable != job.cancellable
            ):
                code = "unknown_handler"
            else:
                async with asyncio.timeout(self.timeout):
                    await handler.execute(Context(self.service, job.id, token, job.attempts))
        except CancellationRequested:
            pass  # complete() observes the persisted request under its row lock.
        except LostClaim:
            return
        except TimeoutError:
            code, retryable = "timeout", True
        except RetryableError:
            code, retryable = "handler_failed", True
        except ExecutionFailure as exc:
            code = exc.code
        except asyncio.CancelledError:
            code = "shutdown" if job.replay_safe else "unsafe_outcome"
            try:
                await self.service.complete(job.id, token, code=code, retryable=True)
            except LostClaim:
                pass
            raise
        except Exception:
            code = "handler_failed"
        try:
            await self.service.complete(job.id, token, code=code, retryable=retryable)
        except LostClaim:
            pass

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            if self.maintenance is not None:
                await self.maintenance()
            await self.service.recover()
            try:
                await self.service.dispatch()
                job_id = await self.service.queue.receive()
            except TransportUnavailable:
                job_id = None
            if stop.is_set():
                break
            if job_id:
                active = asyncio.create_task(self.execute(job_id))
                stopping = asyncio.create_task(stop.wait())
                try:
                    await asyncio.wait({active, stopping}, return_when=asyncio.FIRST_COMPLETED)
                    if stop.is_set() and not active.done():
                        try:
                            await asyncio.wait_for(asyncio.shield(active), self.grace)
                        except TimeoutError:
                            active.cancel()
                    try:
                        await active
                    except asyncio.CancelledError:
                        if not stop.is_set():
                            raise
                finally:
                    stopping.cancel()
                    if not active.done():
                        active.cancel()
                    await asyncio.gather(active, stopping, return_exceptions=True)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), 0.5)
                except TimeoutError:
                    pass
