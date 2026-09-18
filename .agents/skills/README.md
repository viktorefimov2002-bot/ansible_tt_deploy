# TrustTunnel Codex Skills

Repository-specific Agent Skills for development of the TrustTunnel Control Plane project.

## Canonical architecture source

Keep the current Product & Architecture Specification as a normal version-controlled repository document, preferably:

    docs/architecture/product-architecture-spec-v0.1.md

Skills intentionally do not duplicate the full specification.

## Current infrastructure assumptions

For MVP:

- PostgreSQL is the durable source of truth.
- Redis is used for asynchronous jobs, coordination, lightweight events, notifications, locks, and explicitly justified caching.
- Business logic should depend on project abstractions such as JobQueue, EventPublisher, and LockProvider rather than Redis-specific APIs where practical.
- Kafka is not an MVP dependency.
- Kafka or another broker may be considered later if scale or delivery guarantees justify it.
- VictoriaMetrics is the preferred metrics platform.
- Vault is not an MVP dependency.

## Role mapping

Architecture work:

    $tt-project-context
    $tt-architect
    $tt-security

Backend implementation:

    $tt-project-context
    $tt-backend
    $tt-security
    $tt-testing

Frontend implementation:

    $tt-project-context
    $tt-frontend
    $tt-security
    $tt-testing

Data Plane work:

    $tt-project-context
    $tt-dataplane
    $tt-security
    $tt-testing

Infrastructure / Ansible / deployment:

    $tt-project-context
    $tt-infrastructure
    $tt-security
    $tt-testing

Code review:

    $tt-project-context
    $tt-code-review
    $tt-security
    $tt-testing

## Explicit invocation

Example:

    $tt-project-context
    $tt-backend
    $tt-security
    $tt-testing

    Implement TTCP-001.

    Read the Product & Architecture Specification and TTCP-001 first.
    Do not commit or push.
    Run relevant tests and provide the completion report required by the skills.

Codex may also select repository skills automatically when their descriptions match the task.

## Recommended task workflow

For each TTCP task:

1. Start a fresh task/thread when the work is logically independent.
2. Reference the TTCP task explicitly.
3. Invoke only the role skills needed for that task.
4. Let Codex inspect the repository before changing files.
5. Review `git diff`.
6. Run tests.
7. Commit and push only after human review.

## TTCP-001 example

    $tt-project-context
    $tt-backend
    $tt-security
    $tt-testing

    Implement TTCP-001 according to the current Product & Architecture
    Specification and the TTCP-001 task specification.

    First inspect the existing repository and identify the smallest compatible
    implementation.

    Constraints:
    - do not redesign unrelated components;
    - do not commit or push;
    - preserve existing behavior unless TTCP-001 explicitly changes it;
    - add or update tests for changed behavior;
    - keep PostgreSQL as durable source of truth;
    - use Redis-backed project abstractions for async/coordination needs rather
      than introducing Kafka or Redis-specific domain coupling.

    At the end report:
    - changed files;
    - key decisions;
    - tests executed and results;
    - remaining risks/questions.

## TTCP-002 example

    $tt-project-context
    $tt-architect
    $tt-backend
    $tt-security
    $tt-testing

    Implement TTCP-002.

    Treat the Product & Architecture Specification and accepted TTCP-001
    implementation as current context.

    Inspect the repository before making assumptions.

    If TTCP-002 requires a durable architectural decision not already covered
    by the specification, identify it explicitly and state whether an ADR is
    warranted.

    Do not commit or push.

    Run the relevant validation and provide the standard completion report.
