---
name: tt-backend
description: Implement and review TrustTunnel Control Plane backend code, including APIs, services, PostgreSQL persistence, business rules, Redis-backed asynchronous jobs and coordination, audit events, authorization, client configuration management, and server management.
---

# TT Backend

Always combine with:

- `tt-project-context`;
- `tt-security` for externally reachable or privileged behavior;
- `tt-testing` for implementation tasks.

## Design principles

Keep transport/API handlers thin.

Place business rules in explicit service/domain logic rather than duplicating them across handlers.

Keep persistence concerns separate from API representation where practical.

Follow existing repository patterns before introducing new abstractions.

Do not invent a new framework or architectural layer when the current codebase already has an adequate pattern.

## Source of truth

PostgreSQL is the durable source of truth for persistent business state.

Redis may support:

- asynchronous jobs;
- lightweight events;
- notifications;
- temporary coordination state;
- distributed locking;
- caching where explicitly justified.

Do not treat Redis as the authoritative durable store for critical business state.

## API work

For every changed endpoint verify:

- authentication requirements;
- authorization requirements;
- request validation;
- error semantics;
- idempotency where retries are possible;
- audit requirements;
- backward compatibility;
- sensitive-data exposure.

Do not leak infrastructure credentials, SSH secrets, internal tokens, or unnecessary implementation detail through APIs.

## Authorization

Respect role boundaries such as admin and viewer.

Authorization must be enforced server-side.

Do not rely on frontend visibility to protect privileged operations.

## Client configuration rules

Treat the current maximum of 3 configurations per user as a business invariant unless superseded by the specification.

Enforce quota transactionally or otherwise safely against concurrent requests.

Do not depend solely on frontend checks.

## Asynchronous work

Prefer project abstractions such as:

- JobQueue;
- EventPublisher;
- LockProvider.

Avoid direct Redis coupling in domain/business code where an abstraction already exists or is justified.

For asynchronous operations consider:

- stable payload/schema;
- correlation identifiers;
- idempotency;
- retries;
- duplicate delivery/execution;
- timeout/failure state;
- auditability;
- observability.

Avoid putting plaintext secrets into Redis jobs, events, notifications, or cache entries.

Do not introduce Kafka for MVP asynchronous work unless the current architecture specification explicitly requires it.

## Audit

Administrative state-changing operations should produce an audit trail when required by the specification.

Prefer structured audit data including:

- actor;
- operation;
- target;
- timestamp;
- result;
- correlation/request identifier.

Never log passwords, private keys, tokens, or generated client secrets.

## Database changes

For schema changes:

- use the repository migration mechanism;
- consider existing data;
- consider rollback or forward-fix strategy;
- maintain constraints at the database level when they represent critical invariants;
- add tests around changed persistence behavior.

## Completion

Before reporting completion:

1. run relevant unit tests;
2. run relevant integration tests where available;
3. run formatter/linter/type checks used by the repository;
4. inspect the final diff;
5. verify that no secrets or generated local artifacts were added.
