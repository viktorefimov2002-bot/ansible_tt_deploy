import asyncio
import json
from uuid import uuid4

from httpx import ASGITransport, AsyncClient

from apps.api.main import create_app
from apps.jobs.service import JobService
from apps.jobs.worker import Worker
from tests.test_auth import account, auth, key, login  # noqa: F401
from tests.test_jobs import MemoryTransport, create
from tests.test_migrations import database  # noqa: F401


async def test_job_rbac_status_cancellation_sse(auth):  # noqa: F811
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    app = create_app()
    app.state.auth, app.state.jobs = auth, jobs
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, viewer_login, _ = await account(auth, "viewer")
        viewer = await login(client, viewer_login)
        _, admin_login, _ = await account(auth)
        admin = await login(client, admin_login)
        job = await create(jobs)
        path = f"/api/jobs/{job.id}"
        for suffix in ("", "/logs"):
            assert (await client.get(path + suffix)).status_code == 401
        assert (await client.post(path + "/cancel")).status_code == 401
        assert (await client.post(path + "/cancel", headers=viewer)).status_code == 403
        assert (await client.get(path, headers=viewer)).json()["status"] == "queued"
        response = await client.post(path + "/cancel", headers=admin)
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
        transport.available = False
        response = await client.get(path + "/logs", headers=viewer)
        assert response.status_code == 200 and "event: done" in response.text
        assert "Job cancelled" in response.text
        assert response.headers["x-accel-buffering"] == "no"
        assert response.headers["cache-control"] == "no-store"
        response = await client.get(path + "/logs", headers=viewer | {"Last-Event-ID": "1"})
        assert "id: 1\n" not in response.text and "id: 2\n" in response.text
        for cursor in ("-1", "xyz", "100", "1" * 20):
            assert (
                await client.get(path + "/logs", headers=viewer | {"Last-Event-ID": cursor})
            ).status_code == 422
        for suffix in ("", "/logs"):
            assert (
                await client.get(f"/api/jobs/{uuid4()}" + suffix, headers=viewer)
            ).status_code == 404
        assert (await client.get("/api/jobs/not-uuid", headers=viewer)).status_code == 422
        assert (
            await client.post("/api/jobs", headers=admin, json={"type": "shell"})
        ).status_code == 404
        unsafe = await create(jobs, cancellable=False)
        await jobs.claim(unsafe.id)
        assert (
            await client.post(f"/api/jobs/{unsafe.id}/cancel", headers=admin)
        ).status_code == 409


async def test_sse_gap_and_live_events_repaired_from_postgres(auth):  # noqa: F811
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    app = create_app()
    app.state.auth, app.state.jobs = auth, jobs
    job = await create(jobs)
    claim = await jobs.claim(job.id)
    for _ in range(201):
        await jobs.checkpoint(job.id, claim.claim_token, 50)
    await jobs.complete(job.id, claim.claim_token)
    # Simulated Redis restart: only PostgreSQL's bounded suffix remains.
    transport.events.clear()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, body, _ = await account(auth, "viewer")
        headers = await login(client, body)
        response = await client.get(f"/api/jobs/{job.id}/logs", headers=headers)
        assert "event: gap" in response.text
        assert response.text.count("event: log") == 200
        assert '"status": "succeeded"' in response.text
        ids = [int(line[4:]) for line in response.text.splitlines() if line.startswith("id: ")]
        assert ids == sorted(set(ids))


async def test_sse_rechecks_session_and_delivers_redis_events(auth):  # noqa: F811
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    app = create_app()
    app.state.auth, app.state.jobs = auth, jobs
    job = await create(jobs)
    await Worker(jobs).execute(job.id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, body, _ = await account(auth, "viewer")
        headers = await login(client, body)
        response = await client.get(f"/api/jobs/{job.id}/logs", headers=headers)
        events = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert [e["sequence"] for e in events if "sequence" in e] == [1, 2, 3, 4]
        # Revoke after initial authentication but before stream iteration.
        original = auth.authenticate
        calls = 0

        async def expires(token):
            nonlocal calls
            calls += 1
            return await original(token) if calls == 1 else None

        auth.authenticate = expires
        response = await client.get(f"/api/jobs/{job.id}/logs", headers=headers)
        assert "event: auth_expired" in response.text and "event: log" not in response.text


async def test_stream_open_before_worker_execution(auth):  # noqa: F811
    transport = MemoryTransport()
    jobs = JobService(auth.engine, transport, transport)
    app = create_app()
    app.state.auth, app.state.jobs = auth, jobs
    job = await create(jobs)
    subscribed = asyncio.Event()
    original_read = transport.read

    async def read(job_id, after):
        subscribed.set()
        return await original_read(job_id, after)

    transport.read = read

    async def execute():
        await subscribed.wait()
        await Worker(jobs).execute(job.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as client:
        _, body, _ = await account(auth, "viewer")
        headers = await login(client, body)
        task = asyncio.create_task(execute())
        try:
            response = await asyncio.wait_for(
                client.get(f"/api/jobs/{job.id}/logs", headers=headers), 5
            )
            assert "Job completed" in response.text and "event: done" in response.text
        finally:
            await task
