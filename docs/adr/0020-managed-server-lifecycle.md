# ADR-0020: Managed workload lifecycle boundary

- Status: Proposed; implemented for TTCP-017, owner acceptance pending
- Date: 2026-10-02
- Basis: TTCP-017 implementation request, architecture sections 6–9, 19, 23

## Decision

Extend the existing typed ExecutionPort and durable PostgreSQL jobs with exactly
deploy, update, restart and uninstall. API inputs contain explicit release versions,
an idempotency key and, for HTTP ACME deployment, a contact email. No playbook,
shell, sudo command, URL, inventory, filesystem path or arbitrary extra-vars input
is exposed. Job parameters retain non-secret intent; SSH secrets still resolve at
attempt time using the existing encrypted storage and pinned host identity.

Ansible invokes a root-owned, no-argument lifecycle helper with bounded JSON stdin.
Bootstrap installs that helper and copies the existing endpoint role's templates
into root-controlled storage. It also installs distribution Jinja2 and Certbot.
The helper downloads only the official versioned release archive, reads only its
regular endpoint binary member, and uses fixed paths/systemd actions. It never
executes the upstream mutable installer or extracts arbitrary archive paths. TLS
and release-version selection retain the upstream release distribution trust;
this does not introduce independent binary signing or configuration rollback.

Deploy, update and uninstall reconcile intent and may retry up to the existing
three-attempt budget. Remote failures, unreachable nodes and deadlines retry;
invalid contracts fail. Restart is neither replay-safe nor cancellable while
running: worker loss/deadline requires inspection rather than repeating an outage.
All queued jobs can be cancelled; interrupted mutations have uncertain outcome.
The adapter deadline is 600 seconds, worker deadline 620 seconds, claim lease 650
seconds, and node work has a 540-second deadline. The existing local process cleanup
and claim watcher remain in force. Cleanup is not remote rollback.

A nonblocking root-owned workload lock serializes lifecycle and credential helpers,
including surviving remote work after controller loss. PostgreSQL server-row locks
serialize lifecycle admission/target edits. Active credential jobs block lifecycle
admission, and lifecycle intent blocks new credential creation. Uninstall requires
all credentials to have completed revocation, avoiding downloadable stale profiles.

Deployment requires passing current pre-flight checks, including a required DNS
family and all required host/port checks. A report is valid for 15 minutes; any
target/pin edit or fresh pre-flight invalidates it. A fresh pre-flight is required
after uninstall. Workload state is independent of management reachability. Desired
version records accepted intent; confirmed installed version changes atomically
with successful job completion, only after a closed report confirms the running
binary version, systemd state and TCP/UDP listeners. Failed/uncertain outcomes keep
the last confirmed installed version and link to their durable job/failure.

The helper owns only CP-created workloads. It fails closed on an existing foreign
or standalone installation. Uninstall removes its workload directory, systemd unit
and its renewal hook, preserving SSH/user/sudo/bootstrap/exporter/packages/firewall
and certificate state. The node can be deployed again. Update preserves configs,
credentials and certificates. Deploy uses the default endpoint configuration with
empty VPN credentials; credential jobs subsequently provision individual devices.
Configuration revisions, edits and rollback remain TTCP-018.

## Consequences

Apply migration 0010 and rerun bootstrap with the existing management public key
and the complete reviewed repository assets before enabling the new worker.
Operators retain provider recovery access. Legacy CLI/role behavior is unchanged.
No new service, queue, secret store or supervisor is introduced. Initial deployments
use HTTP ACME or existing certificates at fixed Let's Encrypt paths when HTTP ACME
is disabled; other certificate/configuration modes remain standalone workflows.
