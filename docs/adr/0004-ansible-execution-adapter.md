# ADR-0004: Ansible execution boundary

- Status: Proposed; implemented for TTCP-008, owner acceptance pending
- Date: 2026-09-21
- Basis: architecture sections 5–6, 19–20, 23; TTCP-008 implementation request

## Decision

Use a typed ExecutionPort with a closed operation set, an Ansible adapter and a
separate job-handler factory. Execute a fixed repository playbook with exec-style
argv, an isolated temporary inventory/config and a minimal environment. Do not
invoke the CLI wrapper or accept raw inventory, playbook paths, flags or extra-vars.
The standalone Ansible/CLI remains unchanged.

Initially admit read-only server status and offline execution validation only.
Mutating workflows need their own parameter schemas, replay/cancellation policy
and reconciliation before being added. Existing deployment/credential automation
depends on controller files and may expose secret-bearing output; wrapping all
CLI functionality would bypass these requirements.

Use an explicit pinned SSH host key, strict checking, and either an ephemeral
password or unencrypted private key. The caller must obtain/verify the public host
key out of band. Automatic trust enrollment and host-key rotation remain onboarding
work; do not trust keys merely because they were received over an unverified SSH
connection. A future business resolver supplies authorized target/secret material
at attempt time. Jobs and Redis never contain it. No new secret store is introduced.

Capture only reviewed callback event codes. Ansible task names, hosts, module
results, exception strings and raw stdout/stderr are excluded. Unknown output is
replaced with a fixed suppression event. This sacrifices detailed diagnostics to
avoid incomplete secret redaction; adding result fields requires a reviewed schema.
Store/publish these events through the existing PostgreSQL/Redis/SSE path.

POSIX process groups are terminated and reaped on timeout/cancellation/claim loss;
this is not rollback of remote operations. Windows is not an Ansible controller.
Use a worker-specific container stage with Ansible, OpenSSH and sshpass; API keeps
its existing runtime. Temporary files live on the worker's bounded tmpfs.

## Alternatives and consequences

- Shell/CLI forwarding was rejected because it exposes a much larger input surface.
- Ansible Runner would add a dependency/artifact lifecycle; direct subprocesses
  and a minimal callback satisfy the current bounded contract.
- Regex redaction of arbitrary output cannot guarantee secret safety.
- A management agent is still a future ExecutionPort implementation.

No architecture baseline changes or DB migration are required. Native abrupt worker
death can leave remote read-only commands briefly running; expired claims use the
existing recovery policy. Extending this to mutations requires a separate review.

Validation covers schema/injection negatives, secret-safe events and temporary
file cleanup, worker lifecycle against PostgreSQL, plus POSIX process-tree and real
Ansible syntax smoke tests when that runtime is available.
