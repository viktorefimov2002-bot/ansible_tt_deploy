# Release validation — TTCP-021

Scope comes from the explicit TTCP-021 implementation request; there was no separate
task file. Architecture sections 19–27 and phase 11 remain authoritative. This task
adds validation, not product features or an architecture change. The UI is unchanged.

## Required automated gates

`.github/workflows/release-validation.yml` runs on pull requests, pushes and manual
dispatch, on disposable Ubuntu 24.04 runners. It has read-only repository permission,
does not retain checkout credentials, and has no deployment, publication or SSH step.
Configure repository branch protection to require all four jobs after owner approval.

The Linux publication fix and exact-SHA next-push acceptance procedure are in
[CI networking recovery](ci-networking.md).

| Gate | Evidence required |
| --- | --- |
| Python/integration | Locked dependencies; Ruff lint/format; mypy across all `apps`; full Python suite with real PostgreSQL, Redis and NGINX; actual Ansible/process tests on Linux. |
| Persistence | Upgrade, downgrade/re-upgrade, idempotent upgrade, model drift, database guards and concurrent quota checks; no automatic runtime migration. |
| Recovery | Actual Redis process restart and lost queue/event hints; commit while broker is down; killed worker, lease recovery, old-attempt fencing, duplicate delivery and refusal to replay unsafe effects. |
| Web matrix | Both existing applications: frozen pnpm install, lint, format, types, component tests and production static builds. |
| Edge | Existing real NGINX development/production-TLS regressions and actual API/MFA/RBAC tests, plus browser separate-origin denial checks. |
| Compose | Parse base, production, backup and restore overlay combinations; assert private database/broker/API ports, distinct origins and maintenance-only restore secret; build API/worker/collector/edge and run existing runtime smoke. |
| Backup | Actual PostgreSQL 17 dump, streaming encrypted archive, signed HTTPS MinIO storage, conditional upload, readback, wrong-key/tamper/signature denial, empty-target maintenance restore, migration/data checks, reader/runtime grants and restored-access quarantine. |
| Browser E2E | Admin MFA login; server/user creation; invitation; client login; device quota; durable credential request across broker/worker restart; TOML/deep-link/QR delivery; revoke and delivery denial; viewer mutation and cross-origin rejection. |

Run from the repository root on Linux with Python 3.12, Node 24.11.1, pnpm 11.19.0,
local Docker Engine 28+/Compose 2.24.4+, Docker's iptables backend, iproute2 and NGINX installed
(stop any system NGINX first). Network setup requires root or passwordless `sudo -n`
for narrowly scoped iptables rules; missing permissions/backend fail the gate:

Map `admin.localhost` and `vpn.localhost` to `127.0.0.1` in the local hosts file;
CI does this explicitly because Node HTTP requests also need those aliases.

```sh
python -m pip install -r requirements-control-plane-dev.lock -r requirements-execution.lock
python -m ruff check apps tests migrations scripts/ci
python -m ruff format --check apps tests migrations scripts/ci
python -m mypy
python scripts/ci/network_smoke.py
python scripts/ci/python_validation.py
python scripts/ci/compose_validation.py
bash infra/compose/tests/runtime_smoke.sh
bash automation/ansible/tests/layout_smoke.sh
for app in client-web admin-web; do
  pnpm --dir "apps/$app" install --frozen-lockfile
  pnpm --dir "apps/$app" lint
  pnpm --dir "apps/$app" format:check
  pnpm --dir "apps/$app" typecheck
  pnpm --dir "apps/$app" test
  pnpm --dir "apps/$app" build
done
pnpm --dir e2e install --frozen-lockfile
pnpm --dir e2e exec playwright install --with-deps chromium
bash scripts/ci/run_e2e.sh
```

The Python runner creates its own random `ttcp_ci_<random>` database, authenticated
Redis, loopback ports and an egress-blocked task-owned Docker bridge. PostgreSQL 17 client wrappers use
the tools in that exact disposable server image; only ciphertext files leave the dump
pipeline. The required backup harness creates a second disposable MinIO/mc project
with a temporary CA and random keys. Both runners clean up their own containers and
volumes, including on test failure. No operator `.env` or backup configuration is read.

`TTCP_CI=1` validates disposable target names, literal loopback addresses and synthetic
credentials before tests connect. Any test/collection skip fails the CI session.
The Linux Ansible layout gate also rejects its optional `SKIP:` branch.
The main Python invocation explicitly excludes `test_backup_release.py` because the
mandatory next invocation supplies its real object store; neither invocation is optional.
Normal developer runs may still skip missing services/platform tests, and cannot be
used as evidence that the release gates passed.

E2E uses the production TLS NGINX policy, both compiled applications, real API and
durable worker services. Only managed-node execution is replaced by a closed test
adapter accepting one `.invalid` target. It never opens SSH or runs a node helper.
E2E backend services stay on an internal network; NGINX alone also joins the guarded
host-access bridge. Browser egress is restricted to the two test origins, and only
loopback HTTPS is published. This establishes the control-plane
journey; it does not establish VPN connectivity or installation on a real node.

No browser trace, video, screenshot or generated configuration is uploaded. Test
identities/TLS/private material live only in ignored temporary directories and are
destroyed by the harness. Never upload `.tools`, Docker inspect/config output, the
fixture JSON, protected backup configs or browser downloads as diagnostics.

## Security and readiness review

Reviewed boundaries: browser→NGINX→API, admin/viewer/client ownership, API/worker→SQL
and Redis, worker→execution adapter, dump→encryption→remote storage and maintenance
restore→quarantined SQL state. Negative tests exercise actual server authorization,
cookie attributes/origin metadata, route/method allowlists, replay/fencing and grants.
Redis UUID hints remain non-authoritative; restored session/invitation/job state needs
the existing explicit quarantine step before service startup.

The only application edits are monitoring type annotations required by the newly
configured mypy gate. Pydantic's mypy plugin accounts for environment-loaded settings.
No permission, schema, transport or UI contract changed. No ADR is needed for this
validation-only task; existing Proposed ADR acceptance remains an owner decision.

Independent review found that directly launching the backup drill could inherit a
remote Docker context. The harness now explicitly uses the local Unix socket, clears
Docker/Compose/MinIO overrides and supplies a fresh blank Compose environment file.
The initial review was source inspection plus the local evidence below. The first
Linux workflow subsequently exposed a publication defect, recorded and corrected
below; neither source review nor Windows checks certify the Linux gates.

Release sign-off is **pending** until a complete Linux workflow run and the separate
[staging/live acceptance record](runbooks/staging-live-acceptance.md) are available.
Local Windows checks cannot certify Docker/Linux execution. Record actual results,
including failures and environment-dependent skips, rather than declaring an RC ready.

## Implementation-host validation (2026-10-02)

| Actual command/check | Result |
| --- | --- |
| `.venv/Scripts/python.exe .tools/ttcp021-tests.py -q --basetemp=.tools/pytest021-full --tb=short` | 473 passed, 11 skipped; fresh loopback PostgreSQL and real NGINX, including actual migration and logical dump/restore tests. |
| `.venv/Scripts/python.exe -m pytest -q tests/test_ci_e2e.py` | 5 passed; closed execution-fixture negative tests added after the full suite was collected. |
| `.venv/Scripts/python.exe -m pytest -q tests/test_ci_safety.py` | 11 passed; CI target guards and failing test/collection-skip gates. |
| `.venv/Scripts/python.exe .tools/run-backup021-local.py` | 1 passed; actual PostgreSQL 17.11 and TLS MinIO/mc with official binary SHA256 verification; test command is `python -m pytest -q tests/integration/test_backup_release.py` with a fresh basetemp. |
| `python -m ruff check apps tests migrations scripts/ci`; `python -m ruff format --check apps tests migrations scripts/ci` | Passed; 123 Python files formatted. |
| `python -m mypy` | Passed; 65 application files. |
| `pnpm --dir apps/{client-web,admin-web} lint`, `format:check`, `typecheck`, `test`, `build` (each separately) | Both passed; 6 Client Portal and 32 Admin Web component tests, both static builds. Vite initially hit Windows sandbox parent-directory access; tests/builds passed with that access permitted. |
| `python scripts/ci/compose_validation.py --compose-executable .tools/docker-compose.exe` | All five deployment overlay combinations passed. Both standalone CI Compose files also parse successfully. |
| `pnpm --dir e2e install --frozen-lockfile`; `pnpm --dir e2e test:list`; Bash/Node syntax and Prettier checks | Passed; one mandatory browser journey discovered. Discovery is not a browser run. |
| Git Bash `bash automation/ansible/tests/layout_smoke.sh` with Git tools on PATH | Shell/routing/helpers passed; `ttctl init` portion explicitly skipped because that shell lacks Python3/PyYAML. Linux workflow installs these through locked execution dependencies. |
| `git diff --check` and final diff inspection | Passed; existing repository Markdown includes CRLF endings and added lines were corrected. |

The eleven full-suite skips are five new Docker/Redis/worker recovery tests, one
real Redis adapter test, three POSIX/Ansible process tests, one POSIX ownership test
and the dedicated real storage drill (subsequently passed separately). None of these
skips is permitted in the Linux release gate. The ignored local `.tools` launchers
are implementation-host helpers; the supported reproducible harness is under
`scripts/ci/` and requires no local development database or operator inventory.

## Known gaps and release conditions

- Full Linux Docker/MinIO/browser gates were not executed on the implementation host:
  the October 2 host had no Docker Engine/WSL distribution; the October 8 local Ubuntu
  WSL runtime has Docker 24.0.2, below the guarded fixture's Docker 28+ requirement.
  A green mandatory workflow on the exact reviewed commit is required.
- CI cannot certify managed-node bootstrap/deploy/update/rollback, real VPN traffic,
  exporter scraping/live alerts, public DNS/TLS renewal or a real S3 provider. The
  separate checklist requires evidence and restoration/recovery, not just screenshots.
- MinIO validates the implemented protocol against one service/version. Provider
  path-style requests, conditional PutObject, IAM, retention and network failures
  still require a provider-specific rehearsal.
- The smoke stack uses an empty disposable inventory and internal no-op jobs. The
  browser fixture simulates successful endpoint credential operations; it cannot
  establish exactly-once remote side effects or uncertain-outcome reconciliation.
- Images use explicit version tags and Python/pnpm lockfiles; image tags and GitHub
  action major tags are mutable. Immutable image digests/action SHAs and dependency
  hash verification remain supply-chain hardening work; no vulnerability audit was
  performed by this task. Re-run validation on the exact release artifacts.
- Chromium is the automated browser. Additional browsers/mobile TrustTunnel clients,
  production capacity, backup RPO/RTO and cold host-loss recovery need acceptance.
- Backup scheduling/retention, WAL/PITR, global-role/runtime-key escrow and pruning of
  historic job metadata remain the previously documented operational limitations.

