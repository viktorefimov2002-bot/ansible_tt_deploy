# ADR-0013: PostgreSQL jobs and a redis-py asyncio worker

- Status: Proposed; implemented for TTCP-007, owner acceptance pending
- Date: 2026-09-21
- Task: TTCP-007 (implementation request); architecture sections 2.2, 4, 19–20, 33
- Acceptance: not yet recorded

## Decision

Use the existing redis-py client behind JobQueue and EventPublisher, with a small
asyncio worker. PostgreSQL owns intent, attempts, deadlines, cancellation and the
bounded log. No additional worker library is introduced. Write this ADR before
implementation; its Proposed status follows the repository's ADR policy.

Jobs themselves form a durable outbox. Periodically redispatch due queued rows;
Redis carries UUIDs only. Claims lock the PostgreSQL row, increment an attempt and
assign a unique fencing token and a fixed lease. Each handler has a cooperative timeout
shorter than that lease. All writes check the token and unexpired lease. Expired
attempts can be retried only for handlers explicitly declared replay-safe; other
jobs fail with an uncertain-outcome code. This is at-least-once execution, never
a guarantee of exactly-once external side effects. Future adapters must use stable
job IDs as operation keys and reconcile uncertain remote outcomes.

No Redis LockProvider is needed: PostgreSQL row locks and attempt tokens coordinate
this workflow. Target/server serialization belongs to future business workflows.

## Alternatives

- [ARQ](https://arq-docs.helpmanual.io/): fits asyncio and supports retries, but
  stores execution metadata in Redis. Its documented repeated execution still
  requires application idempotency. We would need the same PostgreSQL recovery
  and fencing implementation plus a second lifecycle to reconcile.
- [Celery with Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html):
  broader scheduling/routing ecosystem than this single-operation MVP needs;
  visibility-timeout/redelivery semantics do not remove durable reconciliation.
- Direct redis-py: smallest dependency surface, but we own worker lifecycle tests.
  Re-evaluate a library when scheduling, routing or throughput actually requires it.

## Logs, security and retention

Worker commits a bounded structured history (latest 200 entries) before publishing
entries to bounded Redis streams (200 entries, one-hour TTL). API SSE reads those
events and periodically repairs from PostgreSQL, including after reconnect/loss.
Sequence numbers identify gaps and deduplicate replay. Only predefined safe event
messages are supported initially; raw exception text and handler input are excluded.
Completed job metadata is retained until an explicit retention task is introduced;
per-job history is bounded. Authenticated admin/viewer may inspect; only admin can
request cancellation, which is audited transactionally.

Cancellation is cooperative and enabled only for handlers declaring safe cancellation.
Queued jobs cancel immediately. Running handlers observe a persisted request at
checkpoints; API never claims remote effects were undone. Shutdown stops claims,
waits up to 20 seconds, then interrupts local execution; replay-safe attempts requeue
within their retry budget, unsafe outcomes fail. Crash recovery uses lease expiry.
Pending cancellation at lease expiry fails with an uncertain outcome; it does not
pretend that a lost worker acknowledged a safe boundary.

## Migration and validation

Migration 0004 adds jobs and runtime grants are updated. Deploy migration before
API/worker. Downgrade removes job history and requires stopping workers and explicit
operator intent. Prefer forward fixes. Test row claim races, stale token fencing,
bounded retries, shutdown, cancellation, Redis loss/restart, SSE replay and RBAC.
Only an internal no-op handler ships; execution adapters remain TTCP-008.
