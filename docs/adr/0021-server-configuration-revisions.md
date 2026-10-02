# ADR-0021: Versioned server configuration and node rollback

- Status: Proposed; implemented for TTCP-018, owner acceptance pending
- Date: 2026-10-02
- Basis: TTCP-018 implementation request; architecture sections 17–20, 22–23

## Decision

Use the existing ServerConfigRevision model, server-row admission lock, PostgreSQL
jobs and typed ExecutionPort. Revision creation normalizes a closed schema of two
network policy booleans and five bounded connection timeouts. Revision identity,
content, number, owner and creation metadata are immutable through application and
database guards. Apply state remains mutable; each revision has one durable apply
job. Duplicate requests with the same actor/server/operation idempotency key return
the original result; conflicting input is rejected. To retry a terminal revision
or restore earlier settings, create a new revision from those settings.

Apply jobs store only the revision identity. Workers resolve immutable settings
and encrypted SSH material at attempt time, using the existing pinned host key.
No raw TOML, shell, playbook, variable map, arbitrary path, certificate material or
VPN credential input is accepted. Endpoint address and port stay fixed so existing
client profiles remain usable. Viewer can inspect history and jobs but cannot
create or apply revisions.

The existing root-owned lifecycle helper renders and parses the candidate before
activation. It verifies the fixed configuration shape and retains the previous
healthy configuration under `/etc/ttcp-bootstrap/config-apply/{revision-id}`.
Snapshots and a journal are fsynced before atomic replacement. Restart and process,
TCP and UDP health confirmation precede success. Failed replacement, restart,
health or acknowledgement restores the original bytes, restarts and checks health.
The shared workload lock serializes lifecycle and credential helper mutations.
Credential, certificate, hosts, rules, systemd and VPN-user state are preserved.

A receipt binds the revision and input fingerprint to the result. Healthy duplicate
delivery returns the receipt without another restart; interrupted work restores
the journal's original snapshot rather than recapturing a possibly active candidate.
Each attempt also receives the typed settings of PostgreSQL's last confirmed
revision (or deployment defaults). A new revision can recover an abandoned prior
operation only when its verified journal binds the live candidate to a snapshot
matching that confirmed baseline and binary version. It restores and health-checks
that baseline before taking the new snapshot. Foreign drift without that evidence
fails closed. This prevents rollback to an unconfirmed prior candidate after a lost
apply response. Queued recovery cancellation preserves the prior unconfirmed state.
Confirmed rollback ends the job as failed and does not automatically reapply the
candidate. Lost transport/worker attempts retry within existing bounds. The current
PostgreSQL revision pointer changes only with validated successful job completion.
Rollback failure and unconfirmed outcomes retain the last confirmed pointer and
are explicitly visible; that pointer is not a claim of current runtime health.

## Consequences

Apply migration 0011 before API/worker upgrade. Rerun the reviewed node bootstrap
with the existing management public key to install the updated helper, template and
TOML parser dependency. No new service, queue or supervisor is introduced. Node
snapshots are operation recovery state, not a backup product or off-node backup.
Retention of immutable database history and node receipts needs a future explicit
policy. Native Linux service, SSH and VPN traffic remain rollout acceptance checks.

Admin NGINX explicitly admits the four lifecycle POST endpoints and exactly the
revision collection, detail and apply endpoints. Client-origin isolation remains
enforced at the edge. Admin presentation consumes contracts and state actions so a
future Figma replacement can reuse the configuration flow.
