---
name: tt-dataplane
description: Implement or review TrustTunnel Data Plane and managed-server lifecycle behavior. Use for runtime configuration, reconciliation, server state application, TrustTunnel process management, health reporting, configuration rollout, failure recovery, and Control Plane/Data Plane interaction.
---

# TT Data Plane

Always combine with:

- `tt-project-context`;
- `tt-security`;
- `tt-testing` for implementation work.

## Boundary

The Data Plane executes runtime/network responsibilities.

Do not move management/product business logic into the Data Plane without an explicit architecture decision.

Do not introduce direct dependency on Control Plane private storage merely for convenience.

The Data Plane should not depend directly on Redis unless the architecture explicitly requires it.

Prefer communication through defined Control Plane/Data Plane interfaces.

## Desired state

Where the architecture uses desired-state management, prefer reconciliation over sequences of fragile imperative assumptions.

Operations should be safe to retry where practical.

Repeated application of the same desired state should not corrupt runtime state.

## Configuration lifecycle

For configuration changes consider:

- validation before activation;
- atomic or safe replacement;
- restart/reload requirements;
- rollback or previous-known-good state;
- partial failure;
- concurrent changes;
- version/revision tracking;
- status reporting back to the Control Plane.

Never report successful application before the runtime state is actually known to be usable.

## Process management

Follow the repository's chosen runtime/service mechanism.

Do not invent a second process supervisor.

Handle:

- start;
- stop;
- restart/reload;
- unexpected exit;
- startup failure;
- unhealthy runtime.

## Health

Health should distinguish where useful:

- host reachable;
- management agent reachable;
- TrustTunnel process running;
- configured state applied;
- service actually healthy.

Do not collapse every failure into a generic offline state when the Control Plane can provide better diagnostics.

## Security

Do not expose management interfaces publicly unless architecture explicitly requires it.

Do not persist Control Plane administrative credentials on Data Plane nodes unnecessarily.

Generated client configuration and key material must follow project secret-handling rules.

## Observability

Expose useful metrics compatible with the project's VictoriaMetrics approach.

Prefer bounded-cardinality labels.

Use correlation identifiers for multi-component operations where supported.
