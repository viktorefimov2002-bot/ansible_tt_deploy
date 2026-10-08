# TTCP-021 Linux publication recovery

## Incident and scope

[Actions run 37787314386](https://github.com/viktorefimov2002-bot/ansible_tt_deploy/actions/runs/37787314386)
ran commit `3e8aea19f8948bca43f436cee614ffa0333abc7d` on `control-plane-v0`.
`python-integration` failed while resolving the PostgreSQL binding with `docker port`;
E2E had healthy containers but Chromium reported `ERR_CONNECTION_REFUSED` at
`https://admin.localhost:18443/`. Both web matrix jobs and Compose passed. Python
tests/backup/Ansible acceptance and the browser journey were not reached.

The standalone Python, E2E and MinIO fixtures placed their published endpoints only
on `internal` Docker networks. The in-container health checks succeeded while the
runner-facing publication was unavailable. MinIO shared the same latent defect;
the Python gate failed before it could reach the storage drill. Container health
does not prove host-port publication. [Docker port publishing](https://docs.docker.com/engine/network/port-publishing/)
describes loopback publication and NAT/gateway behavior.

## Fixture networking

`scripts/ci/networking.py` creates a fresh, labelled bridge with a random Linux
interface name, IPv6 disabled, IPv4 NAT publication, masquerading disabled and
default host binding `127.0.0.1`. Every explicit publication also binds `127.0.0.1`.
No product deployment network or service configuration is changed.

Before attaching containers, the helper installs two exact rules for its own bridge:

- `DOCKER-USER`: drop new connections arriving from that bridge and leaving through
  a different interface. Same-bridge peers and established replies remain permitted.
- `INPUT`: drop new container-initiated connections to host services. Replies to
  host-initiated connections remain permitted.

These rules never flush a chain, change a policy or affect unrelated interfaces.
They follow [Docker's iptables user-chain contract](https://docs.docker.com/engine/network/firewall-iptables/).
Attached containers use DNS `127.0.0.1`: Docker's embedded resolver still resolves
their private peers, but does not proxy unknown names to an external DNS server.
[Docker DNS documentation](https://docs.docker.com/engine/network/#dns-services)
defines the container-local meaning of this DNS address. Disabling masquerading
alone is not the egress boundary; the scoped deny rules are mandatory.

The Python PostgreSQL/Redis fixtures use this guarded bridge. MinIO and its bucket
setup container use a separate guarded bridge. E2E PostgreSQL/Redis/API/worker stay
on their private internal network; only NGINX also joins a guarded bridge for HTTPS
publication. Compose's `external` declaration references the freshly created,
owned network, rather than an operator network. Local Docker socket pinning,
environment scrubbing, synthetic identities and `.invalid` node guards remain.

Docker Engine 28+ is required because older releases have a documented localhost
publication isolation defect. Docker's iptables backend, iproute2 and root/passwordless
`sudo -n` are required; there is no permissive fallback. The helper verifies its
network ID, name, owner label, bridge options and IPv6 state before cleanup. It
removes containers/network before the exact firewall rules. If network removal
fails, the rules and protected ownership record remain for recovery and CI fails.
If firewall deletion fails after network removal, the record persists that phase.
`python scripts/ci/networking.py cleanup --state <retained-network.json>` can retry
only after confirming the owned interface is gone; it refuses a reused interface.

## Fail-fast evidence

- Python validates exactly one IPv4 loopback `docker port` result, connects through
  both host ports and executes authenticated PostgreSQL `SELECT 1` and Redis `PING`
  before collecting integration tests. An absent mapping gives a safe explicit error.
- E2E checks both published origins before Chromium: generated certificate trust,
  SNI and Host, each real static bundle title, unauthenticated own API `401` and
  opposite-origin API `404`. The socket destination is literal `127.0.0.1`, independent
  of DNS, and redirects/errors cannot send the probe elsewhere.
- Backup checks the published MinIO HTTPS readiness endpoint using the generated
  CA before bucket setup or dump/restore tests. Failure prevents both downstream steps.
- The mandatory `network_smoke.py` gate exercises real published Redis AUTH/PING,
  same-bridge DNS/peer access and denial against task-owned host/cross-bridge canaries.
  It sends no packet to a production or public test destination. It also verifies
  the exact scoped firewall rule. Backend rules may deny a cross-bridge packet even
  before the user chain; an unchanged forwarding counter alone is not a failure.
- Socket/TLS, rejected public/ambiguous bindings, network ownership, cleanup failure,
  topology and runner ordering regressions accompany the change. E2E now waits for
  logout `204` before reloading, eliminating a second test-only race.

## Verification for the next push

No commit, push or workflow dispatch is performed by this fix. After the owner
reviews and chooses to commit/push:

1. Record the full SHA of the reviewed commit. Open its new **Release validation**
   push run on GitHub. Re-running `37787314386` tests the old SHA and cannot validate
   these local changes. A PR run can use a merge SHA; record that exact tested SHA too.
2. Confirm the run's checked-out SHA matches the reviewed SHA. If the commit changes,
   review and run again; do not combine green checks from different commits/attempts.
3. Require `python-integration`, `web (client-web)`, `web (admin-web)`, `compose` and
   `e2e` all completed with `success` on that run. Cancellation, a skipped gate or an
   earlier green job from another SHA does not satisfy acceptance.
4. In Python logs require the network drill PASS and published PostgreSQL/Redis
   authenticated readiness, the full suite with **zero skips**, the real MinIO drill
   and the Ansible layout gate without `SKIP:`. In E2E require both published HTTPS
   origin-boundary PASS lines and the actual journey passed. Discovery is not a run.
5. If an additional failure emerges, retain its run ID, exact SHA, safe traceback and
   failed step; fix it and repeat all mandatory gates. Never upload environment,
   ownership-directory credential files, browser failure contexts or client configs.

For equivalent local validation use a disposable Ubuntu 24.04 host with Python 3.12,
Node 24.11.1/pnpm 11.19.0, Docker 28+, Compose 2.24.4+, iptables and required privilege.
Run the exact commands in [release readiness](release-readiness.md), including the
network smoke first, with both hosts mapped to IPv4 loopback. Staging/live product
acceptance remains separate; a green CI run never authorizes a live deploy.

## Implementation-host evidence (2026-10-08)

Full Linux CI is not certified locally: WSL is Ubuntu 22.04 with Docker 24.0.2, and
the helper correctly rejects that engine before network/firewall mutation. Current
Windows tests need a fresh workspace `--basetemp` and approved loopback access due
to the sandbox's temporary-directory/socket restrictions. Results are recorded in
the completion report; successful native TLS/NGINX/MinIO tests do not substitute for
the required Docker/iptables/Chromium workflow on the reviewed commit.

| Check actually run | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q tests/test_ci_backup_networking.py tests/test_ci_safety.py tests/test_ci_e2e.py tests/test_ci_e2e_probe.py tests/test_ci_networking.py --basetemp=.tools/pytest-ci-net-complete-20261008 --tb=short -p no:cacheprovider` with native `TTCP_TEST_NGINX` | 69 passed, zero skips; includes real sockets/TLS, actual NGINX and interrupted-cleanup recovery. |
| `.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .tools/pytest-ci-networking-20261008-a3dc9f20 tests/test_ci_networking.py` | 49 passed after cleanup retry/reused-interface regressions were added. |
| `.venv/Scripts/python.exe .tools/run-backup021-local.py` | 1 passed; real PostgreSQL 17.11 plus CA-verified local TLS MinIO readiness and encrypted storage/restore. One existing Windows pytest-cache ACL warning; no test skipped. |
| `python scripts/ci/compose_validation.py --compose-executable .tools/docker-compose.exe` | All five deployment overlay combinations passed. Both updated standalone CI topologies also parse successfully. |
| `python -m ruff check apps tests migrations scripts/ci`; `python -m ruff format --check apps tests migrations scripts/ci`; `python -m mypy` | Passed; application type check covers 65 files. |
| Git Bash `bash -n scripts/ci/run_e2e.sh`; Node syntax; Prettier; `pnpm --dir e2e test:list` | Passed; one browser journey discovered. Discovery is not execution. |
| WSL `python3 scripts/ci/networking.py create --name ttcp-ci-compat-check-20261008 --state .tools/ci-compat-check-20261008.json` | Correctly failed the Docker 28+ requirement on Engine 24.0.2 before mutations; no ownership record created. |
