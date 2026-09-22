# Durable jobs — TTCP-007

Implementation: `apps/jobs/`, `apps/worker/`, `apps/api/jobs.py`, migration `0004_jobs`.
Worker choice and tradeoffs: [ADR-0013](adr/0013-durable-job-worker.md), Proposed.
PostgreSQL is authoritative; Redis queue entries contain UUIDs only. No public
job-creation endpoint exists. Bundled handlers include `internal.noop` and
`internal.execution.validate`; see [TTCP-008 execution integration](execution.md).

## Lifecycle and recovery

`queued → running → succeeded | failed | cancelled`; retry returns to `queued`.
`JobService.create` commits before delivery. Its unique idempotency key identifies
one operation; reusing it for a different target, creator or execution policy fails.
Future business services should insert a Job in the same transaction as business
intent, without calling Redis inside that business transaction. The worker's
dispatcher discovers committed rows automatically.

Each worker runs one handler at a time. Every idle pass recovers up to 100 expired
claims and dispatches up to 100 eligible queued jobs. Delivery is retried every
5 seconds; duplicate hints cannot claim a running/terminal job. Queue storage is
bounded to 1,000 hints with a 60-second TTL. Lost/trimmed hints are reconstructed
from PostgreSQL; dispatch ordering prevents permanent starvation of older rows.

Attempts have a 60-second cooperative timeout and a 90-second lease, using the
database clock. Claims and transitions lock the row; an attempt UUID fences late
writes. There are 3 attempts by default (database maximum 5), with exponential
backoff of 2, 4, 8, 16 seconds. Only an explicit RetryableError, timeout, shutdown
or lost worker can trigger retry, and only with `replay_safe=True`. Other exceptions
fail immediately. Unknown handlers or policy mismatches fail closed.

Redis outage pauses delivery, but in-flight handlers can finish and save logs.
API inspection, cancellation and SSE fallback continue if the already-running
API can reach PostgreSQL. The inherited startup policy requires both dependencies;
Compose restarts failed startup. PostgreSQL failures fail the worker process;
Compose restarts it and lease recovery repairs interrupted execution.

SIGTERM/SIGINT stops new claims, waits up to 20 seconds, then cancels local work.
Async handlers must yield, propagate CancelledError, and finish resource cleanup.
The asyncio timeout cannot forcibly stop blocking or uncooperative handler code.
Unsafe interrupted jobs fail with `unsafe_outcome`; replay-safe work retries within
budget. Fencing does not undo remote side effects. Future TTCP-008 adapters must
provide operation idempotency/reconciliation and subprocess cleanup before opting
into replay or cancellation; server/target locking is outside this task.

## Cancellation and authentication

- `GET /api/jobs/{uuid}`: authenticated admin/viewer, metadata and bounded history.
- `GET /api/jobs/{uuid}/logs`: authenticated admin/viewer, SSE.
- `POST /api/jobs/{uuid}/cancel`: admin only; transactional audit event.

All use the [Bearer session contract](authentication.md). Missing/invalid auth is
401, viewer cancellation 403, missing job 404, malformed UUID/cursor 422. Responses
disable caching. Queued jobs cancel immediately, even if their handler is not
cancellable. Running jobs without safe cancellation return 409. Otherwise a durable
request is set; checkpoints or completion acknowledge it. Repeated requests are
idempotent; terminal status is preserved. Running cancellation returns the current
`running` status and `cancel_requested_at`, never a promise to undo effects.
If the worker lease expires with a pending cancellation, recovery fails the job
with `unsafe_outcome`: a request alone cannot prove a safe checkpoint was reached.

## Live logs

Worker commits structured entries, then publishes through Redis streams. API reads
Redis and repairs missing entries from PostgreSQL every second. Each job keeps the
latest 200 entries in PostgreSQL throughout execution and after completion. Redis
keeps at most 200 entries per stream for one hour after the last write. Job metadata
has no automatic deletion yet; a separate retention decision is required to bound
the total number of historical jobs.

Use streaming fetch with Authorization (native EventSource cannot set that header).
Send `Last-Event-ID: <integer sequence>` to resume. Events are `log` (with monotonic
id), `gap` (bounded history no longer contains some entries), `done` (terminal state),
and `auth_expired` (close and reauthenticate). Heartbeat comments arrive each second.
Sessions are rechecked each iteration. Future cursors are rejected; duplicated or
out-of-order Redis events are merged/deduplicated by sequence. `X-Accel-Buffering: no`
disables NGINX buffering for the stream. Clients must reconnect after network or
database failures. No connection holds a database session while waiting.

Initial logs accept reviewed event codes only, with fixed safe messages and numeric
progress. Raw exceptions, arbitrary handler payloads, SSH output and credentials
are never copied into them. TTCP-008 adds reviewed execution event codes and
suppresses raw output rather than attempting incomplete free-text redaction.

## Deploy and validate

Run `python -m alembic upgrade head` before starting the new API/worker; reapply
`infra/compose/postgres/runtime-grants.sql` for the runtime role. No dependencies or
settings were added. Existing Compose commands remain valid.

```sh
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
python -m compileall -q apps migrations tests
python -m alembic upgrade head --sql
docker compose -f infra/compose/compose.yaml config --quiet
```

PostgreSQL integration: `TTCP_TEST_POSTGRES=1` and `TTCP_POSTGRES_*`, using the
existing isolated-schema fixture. Redis integration additionally requires
`TTCP_TEST_REDIS=1` and `TTCP_REDIS_*`; tests use a random namespace, delete only
their own keys to emulate volatile-state loss, and never FLUSHDB. Real connection
refusal, simulated broker loss/recovery, retries, cancellation, concurrency, SSE
and RBAC tests are included. Full container restart checks use the existing
`bash infra/compose/tests/runtime_smoke.sh` on a host with Docker Engine.
