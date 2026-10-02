# Managed server lifecycle — TTCP-017

Scope is the TTCP-017 implementation request. See
[ADR-0020](adr/0020-managed-server-lifecycle.md) for policy and the privilege boundary.

Apply Alembic migration `0010_server_lifecycle` before upgrading API/worker. Rerun
the reviewed bootstrap with the same public key to install the lifecycle helper,
updated shared-lock credential helper, templates and dependencies. Existing
standalone installations are not automatically adopted or removed.

## API and Admin Web

All four POST endpoints require an authenticated administrator. Viewer can read
server/job state and logs but cannot mutate. Responses are 202 with the existing
job representation. Disabled/unconfigured nodes, conflicting active jobs, stale or
missing pre-flight, missing installed workload and credentials blocking uninstall
return 409. Missing nodes return 404; invalid/extra fields return 422.

| Endpoint under `/api/servers/{id}` | Body in addition to `idempotency_key` |
| --- | --- |
| `/deploy` | `version`, `acme_email` required when the server uses HTTP ACME |
| `/update` | `version` |
| `/restart` | none |
| `/uninstall` | none |

`version` is an explicit numeric `major.minor.patch` upstream release, without `v`,
`latest`, `auto`, prerelease suffixes or command flags. An idempotency key is scoped
to actor/server/operation; reuse returns the original job. Reuse with different
parameters returns 409. A fresh key creates a new request after the server is idle.
PostgreSQL retains intent and audit even when Redis delivery is unavailable.

Admin Web → Servers shows workload state, installed/desired versions and its latest
lifecycle job. Run pre-flight first, enter the exact release and certificate contact,
then Deploy. Update/restart/uninstall have confirmation dialogs explaining outages.
Actions open Jobs for progress, safe logs and failures; refreshing reads authoritative
state. Queued work can be cancelled. Running mutations cannot be cancelled.

`lifecycle_state` is `unknown`, `installed`, `uninstalled`, `failed`, or an operation's
`*_pending` state. `lifecycle_job_id` links its latest job. `preflight_passed_at` is
cleared by target/pin edits, fresh checks, successful deploy or uninstall. A passing
report is valid for admission for 15 minutes. SSH reachability remains `status`.
`trusttunnel_version` is the last confirmed running version, not a guarantee of
current health after a failed operation; `desired_trusttunnel_version` is durable
accepted intent. Inspect Jobs and Monitoring if workload state is failed/unknown.

## Runtime and recovery

Deploy uses existing role templates, fixed `/opt/trusttunnel`, systemd and an empty
credential file. Individual device credential jobs populate it. HTTP ACME requires
DNS and port 80; otherwise provision a certificate through the operator workflow
at `/etc/letsencrypt/live/{domain}/{fullchain.pem,privkey.pem}` first. Firewall policy
remains operator-managed; bootstrap/lifecycle do not open ports automatically.
Updates replace only the endpoint binary and preserve workload configuration.
The versioned archive convention follows the
[official TrustTunnel installer](https://github.com/TrustTunnel/TrustTunnel/blob/master/scripts/install.sh).

Deploy/update/uninstall reconcile repeated attempts and retry bounded failures.
Restart is not automatically replayed. A shared nonblocking root lock rejects
overlapping credential/lifecycle mutations. Controller timeout/worker loss is not
rollback; inspect the node and last job before requesting further work. The node
deadline bounds surviving remote operations even after an SSH connection is lost.

Before uninstall, revoke all server credentials and wait for their jobs. Uninstall
removes only the owned workload/unit/renewal hook. SSH keys/account/sudo helpers,
exporter, provider access, packages, firewall and certificates remain. Fresh passing
pre-flight then permits redeployment. If a partial operation fails, fix the node
prerequisite and retry with a fresh key; an uncertain restart needs operator inspection.
The helper refuses foreign workload paths and unsafe writable/symlinked ancestors.

Configuration revisions and automatic rollback are implemented by
[TTCP-018](server-configuration.md). The four lifecycle POST routes are explicitly
allowed at the Admin NGINX origin and covered through real development/TLS edges.

## Validation record — 2026-10-02

PostgreSQL integration used a disposable loopback cluster and the repository's
random isolated-schema fixture, with `TTCP_TEST_POSTGRES=1`. No managed server or
production infrastructure was contacted. Secrets were supplied through environment
variables, not argv. The disposable cluster was stopped after validation.

| Exact command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | Final full run: 297 passed, 12 environment-dependent skips |
| `.venv/Scripts/python.exe -m pytest tests/test_execution.py tests/test_lifecycle.py tests/test_servers.py tests/test_execution_jobs.py -q` | After completion-contract/type tightening: 87 passed, 3 POSIX skips |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations scripts/node_lifecycle.py scripts/bootstrap_node.py scripts/node_credential.py` | Passed |
| `.venv/Scripts/python.exe -m ruff format --check apps tests migrations scripts/node_lifecycle.py scripts/bootstrap_node.py scripts/node_credential.py` | Passed, 95 files |
| `.venv/Scripts/python.exe -m compileall -q apps migrations tests scripts` | Passed |
| `.tools/uv.exe tool run --cache-dir .tools/uv-cache --python .venv/Scripts/python.exe --from mypy mypy --python-executable .venv/Scripts/python.exe --platform linux --follow-imports=silent --ignore-missing-imports --check-untyped-defs apps/execution apps/jobs/worker.py apps/jobs/service.py apps/servers` | Passed, 15 files; workspace-local UV tool/cache/Python paths |
| `node node_modules/vitest/vitest.mjs run` (Admin Web directory) | 20 passed |
| `node node_modules/typescript/bin/tsc --noEmit` (Admin Web directory) | Passed |
| `node node_modules/eslint/bin/eslint.js .` (Admin Web directory) | Passed |
| `node node_modules/vite/bin/vite.js build` (Admin Web directory) | Passed |
| `node node_modules/prettier/bin/prettier.cjs --check src/components/ServerLifecycle.tsx src/components/AdminView.tsx src/api/admin.ts src/domain/types.ts src/state/useAdmin.ts src/test/lifecycle.test.tsx` | Passed |
| `bash automation/ansible/tests/layout_smoke.sh` (Git Bash, Unix utilities and existing Python shim on PATH) | Passed, including init and standalone CLI routing |
| `git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check` | Passed; CRLF-aware check matches existing Python index line endings |

The first full run encountered an existing TOTP time-step test failure; its unchanged
isolated rerun and the subsequent full suite passed. No authentication change was made.
Windows does not provide the real Ansible/POSIX process, Linux sudo/systemd/ACME
environment here. Live managed-node installation, TLS issuance, service health and
container build remain rollout validation; the portable helper tests exercise real
parsing/rendering/filesystem/recovery logic with OS command boundaries simulated.
