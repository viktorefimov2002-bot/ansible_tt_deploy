# AGENTS.md

## Purpose

This repository currently contains TrustTunnel Ansible/CLI automation and the specification for the target MVP management platform. Treat Control Plane services, PostgreSQL, Redis, workers, frontend applications, and Docker Compose as planned components unless their implementation is present in the repository.

This file defines repository-wide working rules for Codex and other coding agents. Keep it concise. Domain-specific implementation guidance belongs in `.agents/skills/`, while architecture and product decisions belong in `docs/architecture/`.

## Required sources of truth

Before modifying code, inspect the sources relevant to the task.

Primary sources:

1. `docs/architecture/product-architecture-spec-v0.1.md` — architectural and product source of truth.
2. The current `TTCP-*` task specification — scope and acceptance requirements for the task being implemented.
3. Applicable ADRs and technical documentation under `docs/`.
4. Existing code, tests, migrations, configuration, and deployment manifests.
5. Repository skills under `.agents/skills/`.

Do not duplicate the architecture specification into code comments, skills, or other documents unless there is a concrete reason.

If a TTCP task, the architecture specification, and the current implementation disagree, do not silently invent a resolution. Identify the conflict explicitly. Prefer the smallest change that preserves the documented architecture unless the task clearly introduces an approved architectural change.

## Repository skills

Use the repository skills appropriate for the task.

Available TT skills include:

- `$tt-project-context` — shared project constraints and terminology.
- `$tt-architect` — architecture, boundaries, ADRs, flows, tradeoffs.
- `$tt-backend` — Control Plane backend, API, PostgreSQL, Redis-backed async workflows.
- `$tt-frontend` — admin/client UI and frontend integration.
- `$tt-security` — auth, authorization, SSH, secrets, trust boundaries, hardening.
- `$tt-dataplane` — Data Plane lifecycle, runtime state, reconciliation, health.
- `$tt-infrastructure` — Docker, Ansible, PostgreSQL, Redis, VictoriaMetrics, deployment.
- `$tt-testing` — unit, integration, contract, E2E, failure-path and regression testing.
- `$tt-code-review` — correctness, security, architecture and regression review.

For implementation work, combine `$tt-project-context` with the relevant role skill and normally `$tt-security` plus `$tt-testing`.

Do not load unrelated skills merely because they exist.

## Target MVP architectural invariants

These constraints govern the target MVP and its incremental implementation; they do not assert that the corresponding components already exist. Unless the architecture specification explicitly changes them:

- Control Plane and Data Plane responsibilities remain separate.
- Proxy responsibilities remain distinct from management responsibilities.
- PostgreSQL is the durable source of truth.
- Redis is MVP infrastructure for asynchronous jobs, coordination, lightweight events, notifications, locks, and explicitly justified caching.
- Prefer project abstractions such as `JobQueue`, `EventPublisher`, and `LockProvider` over Redis-specific coupling in business logic.
- Kafka is not an MVP dependency.
- VictoriaMetrics is the preferred metrics platform.
- Vault is not an MVP dependency.
- Server provisioning must support SSH password and SSH key flows.
- Authorization includes at least admin and viewer roles.
- Viewer access must not mutate privileged state.
- Client self-service is part of the product direction.
- Each VPN user has a device_limit, defaulting to 3 devices. The quota applies to devices, not credentials or configurations. Each device may have separate credentials/configurations per server. Enforce device_limit rather than a hard-coded value of 3.
- Administrative actions must be auditable where required by the specification.
- Services should remain container-friendly and should not introduce unnecessary barriers to future Kubernetes migration.
- Do not add Kubernetes complexity to the MVP unless a task explicitly requires it.

## Working method

For each implementation task:

1. Read the task specification and relevant architecture/documentation.
2. Inspect the existing implementation before making assumptions.
3. Identify the smallest coherent change that satisfies the task.
4. Reuse existing repository patterns before introducing new abstractions.
5. Implement the change.
6. Add or update tests for changed behavior.
7. Run relevant validation.
8. Inspect the final diff.
9. Report what changed, what was tested, and any remaining risks.

Do not perform broad refactors unrelated to the current task.

Do not redesign neighboring components merely to make the local implementation cleaner.

Do not introduce new frameworks, services, queues, databases, caches, or infrastructure dependencies without a concrete requirement and architectural justification.

Prefer incremental changes that are easy to review and revert.

## Repository discovery

Before inventing commands, paths, frameworks, or conventions, inspect the repository.

Discover build, lint, test, migration, development, and deployment commands from existing sources such as:

- `README*`;
- `Makefile`;
- `Taskfile*`;
- `package.json`;
- `pyproject.toml`;
- `go.mod`;
- `docker-compose*`;
- CI configuration;
- scripts under repository tooling directories.

Do not claim that a command or check exists unless it is present in the repository.

## Code changes

Preserve existing behavior unless the task explicitly changes it.

Keep API handlers/controllers thin where the current architecture supports that pattern.

Keep durable business state in PostgreSQL.

Do not move authoritative business state into Redis.

Keep Redis-specific implementation details behind project abstractions where practical.

Maintain Control Plane / Data Plane boundaries.

Avoid hidden coupling through private storage or undocumented side channels.

Use migrations for persistent schema changes according to the repository's existing migration mechanism.

Do not modify generated files manually unless the repository explicitly expects that workflow.

## Security

Treat authentication, authorization, SSH credentials, generated client configuration, tokens, and secrets as security-sensitive.

Never:

- hardcode production secrets;
- commit private keys, passwords, tokens, or production client configurations;
- write secrets to normal logs;
- expose Redis or PostgreSQL publicly without an explicit architecture requirement;
- rely on frontend visibility as authorization;
- put secret material into Redis jobs/events/notifications unless explicitly required and appropriately protected.

Authorization must be enforced server-side.

For privileged operations, verify both authentication and authorization.

When changing provisioning or remote execution behavior, review failure handling, credential handling, host identity assumptions, retry behavior, and auditability.

## Asynchronous and distributed behavior

For Redis-backed jobs, events, locks, notifications, or similar workflows, consider:

- idempotency;
- retries;
- duplicate execution;
- timeout behavior;
- partial failure;
- correlation identifiers;
- state transitions;
- recovery after Redis restart or temporary unavailability.

PostgreSQL remains authoritative for durable state.

Do not introduce Kafka unless an approved architecture change establishes a concrete need.

## Testing and validation

Run the checks relevant to the files and behavior changed.

Prefer repository-defined commands.

At minimum, consider:

- formatter;
- linter;
- type/static checks;
- unit tests;
- integration tests;
- migration validation;
- API/contract tests;
- E2E tests where relevant;
- infrastructure syntax/idempotency checks for deployment changes.

For bug fixes, add a regression test when practical.

For authorization-sensitive changes, include negative permission tests.

For async behavior, include retry/duplicate/failure-path coverage where relevant.

Never state that tests passed unless they were actually run.

If a relevant check cannot be run, report that clearly.

## Git rules

The default workflow is local changes only.

Do not:

- create commits;
- amend commits;
- rebase;
- merge;
- push;
- force-push;
- create or update remote branches;
- open or merge pull requests;

unless the user explicitly requests that specific Git action.

Do not discard user changes.

Do not reset, checkout over, clean, or otherwise remove uncommitted work unless explicitly instructed.

Before completing implementation work, inspect the final working-tree diff.

## Scope discipline

A TTCP task should normally result in one reviewable logical change.

If unrelated defects are discovered:

- report them;
- fix them only if they block the current task or the user explicitly expands scope.

Do not opportunistically rewrite unrelated code.

If a task requires a durable architectural change that is not already covered by the architecture specification, identify the decision explicitly and state whether an ADR should be created.

## Documentation

Update documentation when behavior, interfaces, deployment steps, or architectural assumptions materially change.

Do not rewrite `product-architecture-spec-v0.1.md` as part of ordinary implementation work.

Architecture specification changes should be explicit and intentional.

Prefer an ADR for durable architectural decisions rather than silently changing implementation conventions.

## Completion report

At the end of implementation work, provide a concise report containing:

### Changed
- files/components changed;
- behavior implemented.

### Decisions
- important implementation or architecture decisions;
- any deviation from existing patterns and why.

### Validation
- exact relevant commands/checks executed;
- their results.

### Remaining
- known limitations;
- unresolved risks;
- follow-up items;
- architecture/specification conflicts discovered.

Do not include a long narrative when a short factual report is sufficient.
