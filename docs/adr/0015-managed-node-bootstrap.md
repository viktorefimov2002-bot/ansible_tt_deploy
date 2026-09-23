# ADR-0015: Managed-node bootstrap privilege boundary

- Status: Proposed; implemented for TTCP-010, owner acceptance pending
- Date: 2026-09-22
- Basis: architecture sections 7, 23–24; TTCP-010 implementation request

## Decision

An operator runs the reviewed `scripts/bootstrap-node.sh` and its sibling Python
implementation locally as root, after password or key login with out-of-band
verified SSH host identity (ADR-0014), or from the provider console. The Control
Plane never receives this initial credential. The only bootstrap input is one
Ed25519 management public key on stdin. No private key is copied to the node.
The API's existing supported key types and host-key types are unchanged.

Use a fixed `ttcp` system account, locked password, private home, root-managed
authorized_keys, public-key-only SSH, and disabled forwarding, PTY and user RC.
Preserve provider recovery access until the operator verifies a new pinned-key
connection. Global password/root-login policy and firewall policy are not silently
changed. The architecture calls for basic hardening without specifying settings;
these are the bounded TTCP-010 settings, not a claim that the specification lists
them verbatim.

The legacy status playbook uses arbitrary root Ansible modules. Granting sudo to
Python or a shell would give unrestricted root access and contradict minimum
privileges. Instead the Control Plane status operation uses a separate playbook
calling a root-owned helper with no arguments. Sudo permits only that helper;
it reads fixed systemd service properties and cannot restart or modify the service.
The existing API result remains management reachability, not VPN health. The CLI
status playbook retains its detailed diagnostics. Status log/certificate parameters
remain accepted for compatibility but are not used by the managed-node probe.
Preflight remains unprivileged. Future mutation tasks must extend this explicit
privilege boundary; bootstrap does not pre-authorize arbitrary future deployment.

Use distribution packages with no recommended packages: Python, sudo and
prometheus-node-exporter. Configure the exporter before package installation so
its post-install start is loopback-only. Scrape transport, firewall exposure,
VictoriaMetrics and dashboards belong to TTCP-011.

## Alternatives and consequences

Unrestricted passwordless sudo and allowing an interpreter were rejected. A new
privileged agent would exceed the task. Reusing the legacy bootstrap would mix
controller setup with managed-node preparation.

Bootstrap is synchronous and operator-run, with a root-owned ownership marker,
exclusive local lock, atomic file replacement and validation before SSH reload.
Failed SSH validation restores only the previous bootstrap SSH fragment; other
partial work is retained for rerun. This is not VPN rollback. Existing accounts,
foreign managed paths and changed keys fail closed. Key rotation is deliberate
operator work followed by audited API credential replacement, never implicit rerun.
Root may modify these controls; this workflow does not attempt to defend against
an already compromised provider/root identity.

No database schema, secret store, job kind or architecture baseline changes.
