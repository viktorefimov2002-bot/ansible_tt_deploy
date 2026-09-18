---
name: tt-security
description: Apply security requirements to TrustTunnel architecture and implementation. Use for authentication, authorization, SSH provisioning, secrets, credentials, client configuration generation, APIs, audit logging, trust boundaries, Redis-backed jobs/events/locks, deployment hardening, threat analysis, and security review.
---

# TT Security

Use this skill as a cross-cutting review layer.

Do not redesign unrelated functionality solely for theoretical hardening.

Prioritize realistic trust boundaries and concrete abuse cases.

## Trust boundaries

Identify:

- browser/client -> Control Plane;
- operator -> Control Plane;
- Control Plane -> managed server;
- Control Plane -> Data Plane;
- service -> PostgreSQL;
- service -> Redis;
- public network -> externally reachable services.

Treat all external input as untrusted.

## Authentication and authorization

Authentication and authorization are separate concerns.

Enforce authorization server-side.

Default privileged actions to explicit permission checks.

Viewer access must not mutate state.

Do not infer authorization from UI state.

## SSH provisioning

The MVP must support password and key-based SSH provisioning.

Treat root/admin SSH credentials as high sensitivity.

Requirements:

- never write passwords/private keys to normal logs;
- never put plaintext credentials into Redis-backed jobs/events/notifications unless the architecture explicitly requires it and provides appropriate protection;
- minimize credential lifetime in memory and persistence;
- avoid returning credentials in generic API errors;
- verify host identity according to the project's chosen SSH trust model;
- clearly distinguish bootstrap credentials from long-lived runtime credentials.

Do not add Vault merely to satisfy this skill; Vault is outside the current MVP unless the architecture specification changes.

## Secrets

Never hardcode secrets into source code.

Never commit:

- passwords;
- private keys;
- API tokens;
- session secrets;
- production client configurations.

Use the deployment/runtime secret mechanism defined by the repository.

If no mechanism exists and one is required by the task, flag it as an architectural decision.

## Redis

Redis is MVP infrastructure for asynchronous jobs, lightweight events, notifications, coordination, locks, and possibly explicitly justified caching.

Security requirements:

- do not expose Redis directly to the public Internet;
- require appropriate authentication/access controls for the deployment;
- use TLS where required by the deployment model;
- keep network access limited to intended services;
- do not use Redis as the durable source of truth for critical business state;
- do not treat Redis as a secret store;
- minimize secret material in jobs, events, notifications, locks, and cache entries;
- define TTLs for ephemeral data where appropriate;
- avoid unbounded key growth and unbounded payload retention.

If Redis is lost or flushed, durable application state should still be recoverable from PostgreSQL and other authoritative stores according to the architecture.

## Audit logging

Security-sensitive administrative operations should be attributable.

Audit events should record enough context to investigate activity without recording secret material.

Security logs and application logs must not become an alternate secret database.

## APIs

Check:

- object-level authorization;
- privilege escalation;
- input validation;
- enumeration/data exposure;
- replay/idempotency concerns;
- rate/abuse considerations where relevant;
- safe error responses.

## Security review output

Report concrete findings with:

- affected location;
- realistic impact;
- evidence/reasoning;
- recommended remediation.

Avoid generic checklist findings with no connection to the changed code.
