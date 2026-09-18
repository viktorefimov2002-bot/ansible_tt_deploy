---
name: tt-infrastructure
description: Implement and review TrustTunnel deployment and infrastructure automation. Use for Docker, Ansible, server bootstrap, SSH access, service deployment, PostgreSQL, Redis, VictoriaMetrics, configuration management, networking, health checks, CI/CD infrastructure, and future Kubernetes-readiness.
---

# TT Infrastructure

Always combine with:

- `tt-project-context`;
- `tt-security`;
- `tt-testing` when automation or deployment behavior changes.

## MVP first

Current scale is small.

Do not introduce Kubernetes, service mesh, operators, Kafka, distributed secret platforms, or complex orchestration solely for hypothetical future scale.

Design components so they can migrate later without requiring them today.

## Server bootstrap

Provisioning must accommodate both:

- SSH password authentication;
- SSH key authentication.

Automation should become idempotent after initial bootstrap.

Do not assume a pristine host unless the task explicitly guarantees one.

Validate required:

- ports;
- DNS;
- host connectivity;
- runtime dependencies.

## Ansible

Prefer declarative/idempotent tasks.

Use handlers where service restart/reload semantics are appropriate.

Do not hide important shell logic inside large opaque shell tasks when native modules or explicit scripts are clearer.

For destructive operations, make intent and scope obvious.

## Containers

Keep services container-friendly and configuration externalized.

Do not bake environment-specific secrets into images.

Use reproducible versions rather than uncontrolled `latest` tags for production-relevant components.

## PostgreSQL

Treat PostgreSQL as the durable source of truth.

Infrastructure should provide:

- persistent storage;
- backups appropriate to project maturity;
- health checks;
- controlled credentials;
- restricted network exposure;
- migration-compatible deployment behavior.

Do not place durable authoritative business data only in Redis.

## Redis

In the target MVP architecture, Redis is the selected infrastructure for the following purposes. Verify its implementation and deployment status in the repository before relying on it:

- asynchronous jobs;
- coordination;
- lightweight event delivery;
- notifications;
- distributed locks;
- explicitly justified caching.

Operational requirements:

- restrict network access;
- configure authentication/access controls;
- use persistence only where explicitly required by the chosen Redis usage model;
- configure appropriate memory limits and eviction policy;
- define TTLs for ephemeral key classes;
- monitor memory, connections, operations, latency, and rejected connections;
- ensure application behavior tolerates restart/loss of ephemeral Redis state according to the architecture.

Do not introduce Kafka unless a later architectural decision demonstrates that Redis-backed abstractions no longer satisfy scale or delivery requirements.

## VictoriaMetrics

Use VictoriaMetrics as the metrics backend unless the architecture changes.

Define useful service and infrastructure metrics.

Avoid high-cardinality labels such as raw user IDs, request IDs, client configuration IDs, or arbitrary error text.

Include Redis and PostgreSQL health/operational metrics where appropriate.

## Kubernetes readiness

Kubernetes readiness means avoiding unnecessary barriers to later migration, for example:

- externalized configuration;
- explicit health checks;
- stateless application processes where practical;
- graceful shutdown;
- durable data outside ephemeral container filesystems.

It does not mean creating Kubernetes manifests before they are needed.

## Completion

For infrastructure changes verify where applicable:

- syntax;
- idempotency;
- dry-run/check mode if supported;
- service health;
- restart behavior;
- rollback/recovery path;
- secret exposure;
- generated diff.
