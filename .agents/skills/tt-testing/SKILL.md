---
name: tt-testing
description: Design, implement, and run tests for TrustTunnel. Use for unit, integration, contract, end-to-end, security-negative, provisioning, Redis-backed async workflow, failure-path, regression, and acceptance testing, or whenever another TT skill changes executable behavior.
---

# TT Testing

Use tests to validate behavior, not implementation trivia.

Prefer the smallest test layer that catches the intended regression reliably.

## Test hierarchy

Use as appropriate:

unit -> component/service -> integration -> contract -> end-to-end

Do not replace all lower-level testing with slow E2E tests.

Do not mock the exact behavior being tested.

## Mandatory concerns

For changed behavior consider:

- happy path;
- validation failure;
- authorization failure;
- missing resource;
- duplicate/retry behavior;
- concurrency where relevant;
- dependency timeout/failure;
- rollback/partial failure;
- backwards compatibility.

## Control Plane APIs

Test role boundaries including at least:

- authenticated permitted action;
- authenticated forbidden action;
- viewer attempting mutation where applicable;
- invalid input;
- nonexistent object;
- ownership/object-level authorization where applicable.

## Client quota

When affected, verify the configuration quota around its boundary.

Test concurrent or repeated creation when the storage/architecture makes races possible.

## Redis-backed asynchronous workflows

For JobQueue/EventPublisher/LockProvider or equivalent abstractions test:

- enqueue/publish/acquire success;
- worker/consumer success;
- duplicate or repeated execution where applicable;
- retryable failure;
- permanent failure;
- timeout or lost worker behavior;
- state transition;
- correlation/audit behavior;
- Redis unavailable/restarted where relevant;
- recovery based on PostgreSQL durable state where applicable.

Prefer tests against project abstractions rather than asserting Redis implementation details unless testing the infrastructure adapter itself.

## Provisioning

Where practical cover:

- successful connection;
- authentication failure;
- unreachable host;
- invalid prerequisites;
- partial installation failure;
- retry after failure;
- repeated/idempotent execution.

Never put real production credentials in fixtures.

## Regression

Every bug fix should normally include a regression test demonstrating the previous failure when practical.

## Completion report

Report exact commands executed and their result.

Do not say "tests pass" if they were not actually run.

If a relevant test cannot be run, state why.
