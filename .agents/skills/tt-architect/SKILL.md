---
name: tt-architect
description: Design or review architecture for the TrustTunnel Control Plane project. Use for component boundaries, Control Plane/Data Plane responsibilities, APIs, data flows, asynchronous workflows, ADRs, deployment topology, scalability decisions, and architectural tradeoffs. Do not use for routine implementation when no architectural decision is involved.
---

# TT Architect

Always combine with `tt-project-context`.

Use `tt-security` when the decision affects trust boundaries, credentials, authentication, authorization, provisioning, or externally reachable interfaces.

## Workflow

Before proposing architecture:

1. Read the current Product & Architecture Specification.
2. Inspect relevant existing code and repository structure.
3. Identify the exact decision being made.
4. Separate current MVP requirements from future scalability requirements.
5. Prefer incremental evolution over speculative infrastructure.

## Required analysis

For a meaningful architectural decision, evaluate:

- responsibility and ownership;
- component boundaries;
- synchronous vs asynchronous communication;
- failure modes;
- retry and idempotency requirements;
- data ownership;
- security boundary;
- observability;
- deployment impact;
- operational complexity;
- migration path;
- compatibility with future Kubernetes deployment.

Do not optimize for Kubernetes at the cost of unnecessary MVP complexity.

## Control Plane / Data Plane

Maintain a clear boundary.

Control Plane should own management intent and administrative workflows.

Data Plane should execute runtime/network responsibilities defined by the architecture.

Avoid hidden cross-boundary coupling such as one component reaching directly into another component's private storage unless explicitly required by the specification.

## Redis and async architecture

For MVP asynchronous or coordination workloads, prefer the project's Redis-backed abstractions rather than coupling business logic directly to Redis commands.

Examples include:

- JobQueue;
- EventPublisher;
- LockProvider;
- notification transport abstractions.

PostgreSQL remains the durable source of truth.

If future scale or delivery semantics require Kafka or another broker, preserve the option to change infrastructure behind abstractions rather than spreading broker-specific APIs across business code.

Do not add Kafka to the MVP without an explicit architecture decision.

## Architecture decisions

When introducing a durable architectural choice, recommend an ADR.

An ADR should contain:

- context;
- decision;
- alternatives considered;
- rationale;
- consequences;
- migration/rollback considerations.

Do not create an ADR for trivial implementation choices.

## Output

For architecture-only work, do not modify production code unless explicitly requested.

Present:

- proposed design;
- affected components;
- important data/control flows;
- tradeoffs;
- unresolved questions;
- whether an ADR is warranted.
