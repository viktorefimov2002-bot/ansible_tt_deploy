# ADR-0022: Durable operational alert cursors in PostgreSQL

- Status: Proposed; implemented for the explicit TTCP-019 request
- Date: 2026-10-02
- Sources: architecture sections 2, 24–25, 30; TTCP-019; ADR-0013, ADR-0016, ADR-0019

## Decision

Evaluate fixed trustworthy monitoring observations in existing worker maintenance.
Persist a singleton cursor per server/type in PostgreSQL, with active incident
UUID and debounce/source clocks. Serialize server/cursor rows and commit audit
transitions atomically. Project through existing safe Audit/Notifications/Admin Web
contracts, correlating opening/recovery by incident UUID. Redis remains transport.

Require fresh collector health before treating node failures/staleness as evidence.
Unknown observations reset pending evidence and preserve incidents. Service/resource
rules require current node evidence, resource recovery uses hysteresis, and gaps
restart debounce. Defer unreliable expiry/saturation/update inputs. See
[rules and deployment](../operational-alerts.md).

## Alternatives and consequences

Memory/Redis debounce loses identity on restart/loss. GET-side evaluation depends
on viewer traffic and mutates state through reads. A new scheduler/broker or
notification-copy table adds coordination without improving this small MVP.
PostgreSQL cursors complement existing immutable audit history and per-account
notification receipts without adding an infrastructure service.

Long worker jobs can delay detection; gaps deliberately restart pending evidence.
No exactly-once external delivery claim or retention change is introduced. Fixed
messages exclude raw labels/secrets. Metrics access is added only to the worker's
private network. Downgrade destroys cursors while retaining history and can cause
new incidents after re-upgrade; prefer forward fixes and ordinary worker restarts.
Control Plane/Data Plane boundaries and the architecture baseline are preserved.
