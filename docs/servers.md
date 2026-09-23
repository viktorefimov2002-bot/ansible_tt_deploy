# Server management and pre-flight — TTCP-009

The task scope is the TTCP-009 implementation request and architecture sections
7–9, 19, 21–23. SSH trust and rotation are resolved in
[ADR-0014](adr/0014-server-ssh-trust.md).

## HTTP contract

All endpoints require the existing bearer session. Viewer may only GET. All other
methods below require admin. Invalid input returns a generic 422 without submitted
values; absent server returns 404; duplicate name, active job, disabled/unconfigured
execution target returns 409. Encryption not configured returns 503.

| Method and path | Behavior |
| --- | --- |
| GET /api/servers?offset=0&limit=100 | Bounded list, limit 1–100 |
| GET /api/servers/{id} | Public metadata and host-key fingerprint |
| POST /api/servers | Create, returns 201 |
| PUT /api/servers/{id} | Replace editable metadata |
| PUT /api/servers/{id}/enabled | `{"enabled": false}` disables; true re-enables |
| DELETE /api/servers/{id} | Logical removal by disabling; metadata/audit retained |
| PUT /api/servers/{id}/credentials | Explicit key/pin replacement |
| POST /api/servers/{id}/status | Durable read-only status job, returns 202 |
| POST /api/servers/{id}/preflight | Durable diagnostic job, returns 202 |

Create/replace metadata fields: `name`, `hostname`, `public_ip`, `domain`, `ssh_user`,
optional `ssh_port` (22), `location` (null), `acme_http` (true). Create also requires
`credentials: {host_key, private_key}`; rotation uses that object's fields directly.
Name is a case-sensitive, database-unique identifier of 1–128 letters, digits,
underscores/hyphens beginning with a letter/digit. Hostnames/domains normalize case
and trailing dots; public_ip must be an IP literal. Domain must be a DNS name.
Passwords, raw inventory, status, revisions and arbitrary execution parameters are
not accepted. Root is not an allowed management user. Different records may refer
to the same host; uniqueness applies to the existing Server name invariant.

PUT is replacement, not PATCH: omitted optional fields reset to their defaults.
Disable/DELETE changes Control Plane metadata only, never uninstalls or stops a
remote service or revokes VPN credentials. Physical deletion and retention policy
are deferred. Re-enabling resets status to pending and requires fresh diagnostics.
Metadata/credential changes also invalidate cached reachability. No API accepts a
caller-supplied runtime status.

Job endpoints accept `{"idempotency_key": "operator-generated-request-id"}` (1–64
ASCII letters/digits/underscore/hyphen). Deduplication is scoped to actor, server,
operation and key. Reuse returns the original job, including after completion.
Use a fresh key to request fresh diagnostics. Server-row locking serializes job
admission and mutations; only one active job per server is admitted. Cancel through
the existing jobs API before editing a queued/running target. Creation and audit
are atomic in PostgreSQL; the durable dispatcher supplies the Redis delivery hint.
Both operations are read-only, replay-safe and cancellable with the TTCP-007 lease
and claim fencing. They do not deploy, bootstrap, restart or uninstall anything.

## Structured pre-flight results

GET /api/jobs/{id} exposes `result` with `outcome`, `attempt`, `checks`, and `ready`.
All eleven checks are present. Values are `pass`, `fail`, `unknown`, `skipped`.
An absent observation is unknown, never silently successful. `ready` requires all
checks to pass or be legitimately skipped. A collected report completes the job
even when checks fail or execution times out; inspect ready/checks/outcome, not
just job.status. Resolver/invalid-contract failures remain job failures. Reports
replace earlier attempts under the active claim and never accumulate raw output.

| Check | Policy |
| --- | --- |
| ssh | Successful remote probe; unreachable is fail, unobserved is unknown |
| dns_a / dns_aaaa | Controller DNS lookup, 5-second budget per family; configured public_ip family must resolve exclusively to that address; other family checks resolution only; absent optional family is skipped |
| tcp_80 | Local IPv4/IPv6 bind availability, skipped unless ACME HTTP challenge is configured |
| tcp_443 / udp_443 | Local IPv4/IPv6 bind availability, always required |
| os | Ubuntu 22.04/24.04 or Debian 12/13 |
| architecture | x86_64 or aarch64 |
| disk | At least 1 GiB free on / |
| memory | At least 512 MiB physical RAM |
| time_sync | timedatectl NTPSynchronized=yes; unavailable tooling is unknown |

These explicit MVP prerequisite thresholds are conservative policy chosen for this
task, not a capacity guarantee. The deployment adapter's general Linux check is
broader; this diagnostic admission policy intentionally names reviewed distributions.
Additional DNS addresses in the configured family fail to avoid deploying with
round-robin records pointing elsewhere. A second-family address is not verified
against server interfaces because the model currently has one public_ip.

Port checks briefly bind without listening or sending packets. When an unprivileged
user cannot bind a privileged port, Linux /proc/net socket tables determine occupancy;
unreadable/malformed tables produce unknown. Occupied ports fail (including an already deployed service). No sudo,
package installation or privileged configuration changes are attempted. These are
pre-deployment availability checks, not external firewall/NAT reachability probes.
Remote Python 3.10+ and timedatectl are expected on supported distributions.

Status uses the TTCP-008 resolver and existing playbook. It records execution
outcome and updates server status to reachable on successful execution, otherwise
unknown. Reachable means management reachability, **not VPN health**. `last_seen_at`
advances only for an observed successful remote check. Diagnostic reports cannot
authorize a future deploy on their own; freshness and rollout gating belong to the
deployment task.
TTCP-010 status uses `managed-status.yml` and the fixed root-owned status helper
installed by [managed-node bootstrap](node-bootstrap.md). It requires only that
helper's no-argument sudo permission. The legacy CLI status playbook is unchanged;
pre-flight does not require sudo. Existing managed nodes must install the new helper
before running status with the upgraded worker.

## Operations

Apply `alembic upgrade head` before starting upgraded API/workers. Migration 0005
adds nullable SSH fields and job result, plus acme_http with a true default. It
does not backfill credentials or contact existing servers. Supply the same protected
TTCP_AUTH_ENCRYPTION_KEY to API and worker; Compose now forwards it to both.
The worker image includes the fixed preflight playbook and custom probe module.
Worker also joins a dedicated non-internal management_egress network for outbound
SSH/DNS, with no published ports. PostgreSQL and Redis remain on internal networks.

Testing uses generated ephemeral keys, injected execution/DNS/probe boundaries and
the existing isolated-schema PostgreSQL fixture. Live Linux/Ansible/SSH and firewall
validation must be performed on a disposable prepared node before production use.

### Validation record (2026-09-22, Windows controller)

PostgreSQL integration ran against a new disposable loopback cluster on port 55439,
using the existing per-test isolated-schema fixture (`TTCP_TEST_POSTGRES=1`).
Test credentials were supplied through environment variables. No managed node was
contacted. The cluster was stopped after validation.

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 159 passed, 4 skipped |
| `.venv/Scripts/python.exe -m pytest tests/test_servers.py tests/test_preflight.py -q` | Final port-permission and input-validation changes: 30 passed |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations` | Passed |
| `.venv/Scripts/python.exe -m ruff format --check apps tests migrations` | Passed, 56 files |
| `.venv/Scripts/python.exe -m compileall -q apps migrations tests` | Passed |
| `.tools/docker-compose.exe -f infra/compose/compose.yaml config --quiet` | Passed with disposable environment values; rendered JSON network isolation assertions also passed |
| `git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check` | Passed; preserves existing CRLF files |

The four full-suite skips require POSIX process management (two tests), actual
Ansible (one), and Redis integration (one). Docker CLI/engine and a Linux Ansible
controller are unavailable here, so container runtime/build and live SSH are not
validated. No commits or pushes were created.
