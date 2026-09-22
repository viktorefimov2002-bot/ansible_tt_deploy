import asyncio
from uuid import uuid4

import pytest

from apps.execution.ansible import AnsibleExecutionAdapter
from apps.execution.jobs import execution_handlers
from apps.execution.ports import ExecutionRequest
from apps.jobs.service import CancellationRequested, LostClaim
from apps.jobs.worker import Worker
from tests.test_execution import target
from tests.test_jobs import create, due, expire, jobs  # noqa: F401
from tests.test_migrations import database  # noqa: F401


class Resolver:
    async def resolve(self, target_id):
        assert target_id is not None
        return ExecutionRequest(operation="server.status", target=target())


@pytest.mark.parametrize(
    "code,error", [(0, None), (2, "execution_failed"), (4, "execution_unreachable")]
)
async def test_job_process_result_events_duplicate_and_redis_loss(jobs, code, error):  # noqa: F811
    calls = []

    async def runner(argv, cwd, env, output, deadline):
        calls.append(argv)
        jobs.events.available = False
        await output(b"execution_task_ok\nprivate-key-secret\n", False)
        return code

    job = await create(jobs, kind="server.status", target_type="server", target_id=uuid4())
    worker = Worker(jobs, execution_handlers(AnsibleExecutionAdapter(runner=runner), Resolver()))
    await worker.execute(job.id)
    await worker.execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == ("succeeded" if error is None else "failed")
    assert saved.error_code == error
    assert len(calls) == 1
    assert "execution_task_ok" in [entry["code"] for entry in saved.history]
    assert "private-key-secret" not in str(saved.history)
    assert "fixture-secret" not in str(saved.history)


@pytest.mark.parametrize("stop", ["cancel", "timeout", "claim_lost", "shutdown"])
async def test_job_stops_silent_execution_before_finalizing(jobs, stop):  # noqa: F811
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def runner(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    job = await create(jobs, kind="internal.execution.validate")
    worker = Worker(
        jobs,
        execution_handlers(AnsibleExecutionAdapter(runner=runner)),
        timeout=0.5 if stop == "timeout" else 5,
    )
    task = asyncio.create_task(worker.execute(job.id))
    await asyncio.wait_for(started.wait(), 3)
    if stop == "cancel":
        await jobs.cancel(job.id, uuid4(), "test-cancellation")
    elif stop == "claim_lost":
        await expire(jobs, job.id)
    elif stop == "shutdown":
        task.cancel()
    if stop == "shutdown":
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await asyncio.wait_for(task, 3)
    assert cleaned.is_set()
    saved = await jobs.get(job.id)
    assert (
        saved.status
        == {
            "cancel": "cancelled",
            "timeout": "queued",
            "claim_lost": "running",
            "shutdown": "queued",
        }[stop]
    )


async def test_event_validation_and_fencing(jobs):  # noqa: F811
    job = await create(jobs)
    claim = await jobs.claim(job.id)
    with pytest.raises(ValueError):
        await jobs.execution_event(job.id, claim.claim_token, "password=bad")
    with pytest.raises(LostClaim):
        await jobs.execution_event(job.id, uuid4(), "execution_task_ok")
    await jobs.cancel(job.id, uuid4(), "cancel")
    with pytest.raises(CancellationRequested):
        await jobs.check_execution(job.id, claim.claim_token)


async def test_adapter_deadline_is_structured_and_not_automatically_retried(jobs):  # noqa: F811
    async def runner(*args):
        raise TimeoutError("secret")

    job = await create(jobs, kind="internal.execution.validate")
    await Worker(jobs, execution_handlers(AnsibleExecutionAdapter(runner=runner))).execute(job.id)
    saved = await jobs.get(job.id)
    assert saved.status == "failed" and saved.error_code == "execution_timeout"


async def test_invalid_job_target_and_policy_do_not_start_process(jobs):  # noqa: F811
    async def forbidden(*args):
        pytest.fail("Invalid job executed")

    handlers = execution_handlers(AnsibleExecutionAdapter(runner=forbidden))
    for changes in ({"target_id": uuid4()}, {"cancellable": False}):
        job = await create(jobs, kind="internal.execution.validate", **changes)
        await Worker(jobs, handlers).execute(job.id)
        assert (await jobs.get(job.id)).error_code in {"execution_invalid", "unknown_handler"}
