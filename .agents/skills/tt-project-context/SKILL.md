---
name: tt-project-context
description: Apply the shared TrustTunnel Control Plane project context, architectural invariants, terminology, scope boundaries, and source-of-truth rules. Use for any implementation, design, review, testing, infrastructure, security, or planning task in the TT repository.
---

# TrustTunnel Project Context

Use this skill together with the role-specific TT skill appropriate for the task.

## Sources of truth

Before making architecture-affecting decisions:

1. Read the current Product & Architecture Specification in the repository.
2. Read applicable ADRs and task specifications such as TTCP-*.
3. Inspect the current implementation before assuming structure or behavior.
4. Follow repository-local AGENTS.md instructions.

Do not duplicate the architecture specification inside this skill.

If the implementation and specification conflict, identify the conflict explicitly instead of silently choosing one.

## Project invariants

Treat these as current architectural constraints unless the repository specification explicitly supersedes them:

- The system separates Control Plane and Data Plane responsibilities.
- Proxy responsibilities are architecturally distinct from management responsibilities.
- Initial deployment is small: approximately 2 servers, growing to roughly 2-4.
- MVP initially assumes a single operator.
- Authorization includes at least admin and viewer roles.
- Client self-service is part of the product direction.
- A user may have at most 3 active client configurations unless the specification changes this limit.
- Server provisioning must support SSH password and SSH key flows.
- Redis is the preferred MVP infrastructure for asynchronous jobs, coordination, lightweight event delivery, notifications, and distributed locks through project abstractions such as JobQueue, EventPublisher, and LockProvider.
- PostgreSQL remains the durable source of truth.
- Redis must not become the authoritative store for durable business state.
- Infrastructure-specific coupling should be minimized so Redis can later be complemented or replaced by another transport, including Kafka, if scale or delivery guarantees justify it.
- Kafka is not an MVP dependency.
- VictoriaMetrics is the preferred metrics platform.
- Vault is intentionally not an MVP dependency.
- Administrative operations require auditability.
- UI notifications are part of MVP/product scope; Telegram may be added later.
- Services should be container-friendly.
- Architecture should permit future Kubernetes migration without forcing Kubernetes complexity into the MVP.

## Engineering behavior

Prefer the smallest implementation that satisfies the current task and architecture.

Do not introduce infrastructure, frameworks, databases, queues, caches, service boundaries, or protocols merely because they may be useful later.

Do not redesign unrelated parts of the repository.

Preserve backward compatibility unless the task explicitly changes an existing contract.

Never commit or push changes unless the user explicitly requests it.

The normal workflow is:

edit locally -> validate -> test -> inspect diff -> report to user

The user remains responsible for deciding whether to commit and push.

## Completion report

For implementation tasks, finish with:

- changed files;
- important implementation decisions;
- tests/checks executed;
- known limitations or follow-up work;
- specification or architecture questions discovered during implementation.

Keep the report concise.
