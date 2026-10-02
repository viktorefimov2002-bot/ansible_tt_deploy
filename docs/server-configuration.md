# Server configuration revisions — TTCP-018

Scope follows the implementation request and architecture sections 17–18. The
recovery decision is recorded in [ADR-0021](adr/0021-server-configuration-revisions.md).

Apply Alembic migration `0011_config_revisions` before upgrading API/worker.
Rerun the reviewed bootstrap with the existing management public key and updated
repository assets to install the lifecycle helper, VPN template and `python3-tomli`
fallback for nodes whose system Python predates `tomllib`.

## API

All routes below are relative to `/api/servers/{server_uuid}`. Reads require an
authenticated admin or viewer; POST requires admin. Browser calls retain the
existing host-only session and same-origin header requirements.

| Route | Method | Body / result |
| --- | --- | --- |
| `/config-revisions?offset=0&limit=50` | GET | Bounded newest-first revision array |
| `/config-revisions/{revision_uuid}` | GET | Owned revision and safe apply state |
| `/config-revisions` | POST | `{config: {...}, idempotency_key: "..."}`; validated immutable revision |
| `/config-revisions/{revision_uuid}/apply` | POST | `{idempotency_key: "..."}`; 202 durable job |

Only these exact routes are added to the Admin NGINX allowlist. The four
TTCP-017 lifecycle POST routes (`deploy`, `update`, `restart`, `uninstall`) are also
explicitly admitted. Unsupported suffixes/methods and Client-origin calls fail at
NGINX before reaching the API.

Supported settings are non-secret and structured. Missing fields receive defaults.
Unknown fields, string/number boolean coercion, non-integer timeouts and out-of-range
values fail validation. The node revalidates the complete normalized schema.
Legacy history with invalid settings returns safe metadata, `config: null` and
`validation_valid: false`; it cannot be applied. A malformed legacy current revision
also prevents apply because a valid previous baseline is required.

| Setting | Default | Allowed values |
| --- | --- | --- |
| `ipv6_available` | true | Boolean |
| `allow_private_network_connections` | false | Boolean |
| `tls_handshake_timeout_secs` | 10 | Integer 1–120 |
| `client_listener_timeout_secs` | 600 | Integer 1–86400 |
| `connection_establishment_timeout_secs` | 30 | Integer 1–600 |
| `tcp_connections_timeout_secs` | 604800 | Integer 1–2592000 |
| `udp_connections_timeout_secs` | 300 | Integer 1–86400 |

Revision creation allocates a per-server sequence under the server row lock.
Content and creation metadata cannot be edited or deleted through runtime SQL.
Creation does not alter production. Apply requires an enabled SSH-configured node
with a confirmed installed workload and no conflicting server/credential job.
The job contains only a revision UUID; credentials never enter queue payloads.

Same-key retries return the same revision/job; reuse with different content returns
409. Each revision can be submitted once, including a cancelled or failed request.
Create a new revision for a new attempt or to activate earlier settings. Missing
servers or cross-server revisions return 404; denied roles 403; invalid input 422.

## Activation and recovery

The helper uses the existing fixed root paths, pinned SSH boundary, workload lock
and systemd service. It renders and parses a candidate before any production write,
confirms the previous workload is healthy, persists the prior configuration and
journal, atomically replaces `vpn.toml`, restarts and verifies running version and
TCP/UDP listeners. Credentials, user/device rows, SSH/certificate material, hosts,
rules and service unit are not rewritten by configuration apply.

Any replacement/restart/health/receipt error triggers restoration of the previous
bytes and a restart/healthcheck. The job remains failed after a successful rollback.
`rolled_back` confirms restoration and health; `rollback_failed` requires operator
inspection. The current pointer changes only after a validated applied report and
successful durable completion. Unconfirmed worker/transport outcomes are explicit
and retain the last confirmed pointer without implying health.

Node recovery files live in `/etc/ttcp-bootstrap/config-apply/{revision_uuid}`:
`previous.toml`, `journal.json`, `receipt.json`. They are root-controlled operation
state. Repeated delivery validates the fingerprint, active file and health without
repeating a successful restart. An interrupted journal restores the original
snapshot; confirmed rollback never causes automatic candidate reapplication.
For a new revision after an unconfirmed attempt, the worker supplies the last
confirmed settings internally. The node accepts its current file as the baseline
only if those settings match. Otherwise it restores a verified prior journal whose
candidate matches the live file and whose snapshot matches the confirmed settings
and binary version. It confirms health before starting the new apply. Unknown drift
without such evidence fails closed and requires local inspection. Cancelling a
queued recovery request retains the preceding unknown/rollback-failed server state.
Do not remove recovery files while jobs may retry. Retention is deferred.

Queued applies can be cancelled before execution. Running applies are not
cancellable. The node reserves 90 seconds for rollback after its 420-second apply
deadline and hard-stops at 510 seconds, inside the 600-second controller deadline.
Worker/process termination is not evidence that rollback completed; inspect
the revision/job state and node if the result is unconfirmed. Rollback does not
guarantee uninterrupted VPN connections.

## Admin Web

Servers → Configuration revisions loads immutable history and current/apply state.
Admin can edit network policy, expand bounded timeouts, create a validated revision
and separately confirm Apply. Jobs shows safe progress, failure and rollback state.
Use revision settings copies older settings into a new draft. Viewer sees history,
current state, safe failures and job links without mutation controls. Loading, empty,
error, pending and pagination states are included. HTTP/state/domain code remains
outside presentation components for the future Figma visual replacement.

## Validation

Use the repository Python lint/format commands and `pytest`, enabling disposable
PostgreSQL integration with `TTCP_TEST_POSTGRES=1` and `TTCP_POSTGRES_*`.
`TTCP_TEST_NGINX` selects a real NGINX executable for development and production TLS
edge regressions. Revision tests exercise durable apply/rollback, immutability,
concurrency, idempotency, claim fencing, retries, cancellation, RBAC and preservation.
Node tests use real render/parse/filesystem logic with simulated Linux OS boundaries.
Admin Web uses its existing typecheck, lint, format, Vitest and Vite build commands.

Native Linux sudo/systemd, SSH host identity, live workload restart/health and VPN
traffic require deployment acceptance. Backups and general operations are out of scope.

## Local validation record — 2026-10-02

Validation used disposable PostgreSQL on loopback port 55418 with randomized schema
fixtures and real NGINX with temporary TLS certificates. Test credentials were
supplied through ignored local environment setup, not command-line arguments. No
managed node or production infrastructure was contacted. The cluster was stopped
after validation. Commands ran from the repository root unless marked Admin Web.

| Exact command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q --basetemp=.tools/pytest018-full --tb=short` | 405 passed, 5 environment-dependent skips |
| `.venv/Scripts/python.exe -m pytest tests/test_config_revisions.py tests/test_execution_config.py tests/test_node_config.py tests/test_node_lifecycle.py tests/test_node_bootstrap.py -q --basetemp=.tools/pytest018-final-recovery --tb=short` | After final recovery/deadline changes: 150 passed, 1 POSIX skip |
| `.venv/Scripts/python.exe -m pytest tests/test_client_edge.py -q --basetemp=.tools/pytest018-edge-api-checked` | 13 passed, no skips; real NGINX → API/MFA/RBAC and development/production TLS route regressions |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations scripts/node_lifecycle.py scripts/bootstrap_node.py scripts/node_credential.py` | Passed |
| `.venv/Scripts/python.exe -m ruff format --check apps tests migrations scripts/node_lifecycle.py scripts/bootstrap_node.py scripts/node_credential.py` | Passed, 100 files |
| `.venv/Scripts/python.exe -m compileall -q apps migrations tests scripts` | Passed |
| `.tools/uv.exe tool run --offline --cache-dir .tools/uv-cache --python .venv/Scripts/python.exe --from mypy mypy --python-executable .venv/Scripts/python.exe --platform linux --follow-imports=silent --ignore-missing-imports --check-untyped-defs apps/execution apps/jobs/worker.py apps/jobs/service.py apps/servers` | Passed, 16 files; UV tool/cache paths kept inside workspace |
| `node node_modules/vitest/vitest.mjs run` (Admin Web) | 26 passed |
| `node node_modules/typescript/bin/tsc --noEmit` (Admin Web) | Passed |
| `node node_modules/eslint/bin/eslint.js .` (Admin Web) | Passed |
| `node node_modules/vite/bin/vite.js build` (Admin Web) | Passed |
| `node node_modules/prettier/bin/prettier.cjs --check src/components/ServerConfiguration.tsx src/components/AdminView.tsx src/domain/types.ts src/domain/configuration.ts src/api/admin.ts src/state/useAdmin.ts src/state/useServerConfiguration.ts src/test/configuration.test.tsx` (Admin Web) | Passed |
| `node .tools/config018-ui-qa.cjs` | Headless Edge desktop/mobile create, confirmed apply, rollback job and viewer history; no JavaScript errors, page overflow or persisted browser data; screenshots inspected |
| `bash automation/ansible/tests/layout_smoke.sh` (Git Bash, `/usr/bin`, `/bin` and existing Python shim on PATH) | Passed: shell syntax, CLI routing, arguments, exit status, local credentials and generated helpers |
| `git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check` | Passed; final tracked diff and new files reviewed |

Windows skips cover real POSIX Ansible/process/bootstrap behavior; real Redis was
unavailable. PostgreSQL-backed job recovery, idempotency and claim fencing ran with
the repository transport abstraction. Linux SSH/sudo/systemd and native VPN traffic
remain rollout acceptance. No commits, pushes, backups or general operations added.
