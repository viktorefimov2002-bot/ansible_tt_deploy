# ADR-0019: Admin visibility and durable notification receipts

- Status: Proposed; implemented for the explicit TTCP-016 request
- Date: 2026-10-01
- Sources: architecture sections 21–25, 27; TTCP-016; ADR-0016 and ADR-0018

## Context and decision

Operational visibility needs durable audit/notification history and bounded
monitoring data on the existing admin origin. Keep the public boundary in
authenticated FastAPI endpoints. A read-only backend adapter queries the private
TTCP-011 VictoriaMetrics stack with fixed instant/range queries and constrained
windows. Distinguish dependency, collector, node and service health; apply source
timestamps to freshness. The browser cannot query metrics storage or PostgreSQL.

Use existing PostgreSQL audit events as the durable notification event identity.
Project a selected set of operational/security events into fixed notification
text, with no raw audit metadata, tokens, arbitrary messages or error details.
Terminal jobs append audit events in the same transaction as their durable final
state; retries remain nonterminal. PostgreSQL notification receipts reference the
event and current admin account through a composite key. A notification is the
selected event plus that account's optional receipt. Both event and receipt data
survive Redis failure and replay without duplicate notification identities.

Authorized audit, notification and monitoring GETs do not mutate visibility or
managed state; existing authentication/authorization denials remain auditable.
Only administrators may explicitly PUT their own notification read/unread state;
repeat requests are idempotent. Viewer remains read-only, including notification
bookkeeping. A read
receipt does not acknowledge or hide an event for another operator. The NGINX
admin allowlist adds only exact GET paths and the UUID read-state PUT route; the
VPN allowlist is unchanged. Existing host-only cookie and CSRF rules apply.

## Alternatives and consequences

A separate notification-copy table and fan-out worker would add duplicate durable
event state and recovery coordination without improving this small deployment's
delivery needs. A Redis-only feed would lose history and receipts. Creating alerts
during GET requests would mutate state for viewers and make event production
depend on page traffic. A generic VictoriaMetrics proxy would expose unrestricted
queries and infrastructure details. These alternatives are not selected.

Polling can replay historical matching events from PostgreSQL without importing
them or relying on Redis notification delivery. Fixed templates deliberately
trade detailed context for safe rendering of historical audit rows. Audit APIs
omit metadata entirely. Offset pagination is bounded but is not a snapshot; new
events can shift later pages. Notification events and receipts currently follow
audit history retention; there is no automatic pruning or shared dismissal.

The migration adds indexed receipt persistence and indexes supporting the new
reads. Downgrading removes receipts and those indexes while preserving audit
history; unread state is reset if the migration is later reapplied. Future event
types can extend the safe projection, or introduce a dedicated notification entity
if independent delivery/history requirements justify it.

No new infrastructure service, broker or Data Plane control is introduced.
Automatic node/service/resource-threshold, certificate, update and backup alerts
still need event producers; observing unavailable metrics alone does not create a
durable incident. Telegram and lifecycle operations remain outside this task.
