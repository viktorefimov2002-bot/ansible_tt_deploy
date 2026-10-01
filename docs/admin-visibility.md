# TTCP-016 — Admin monitoring, audit and notifications

Admin Web adds Monitoring, Audit and Notifications to the separate
`admin.<domain>` application. Use the existing [Admin Web runtime and sign-in
instructions](admin-web.md). Apply migrations before rebuilding the API, worker
and web edge:

```sh
cd infra/compose
docker compose run --rm --build migrate
# Reapply restricted runtime role grants now, when used (see below).
docker compose up -d --build api worker nginx
docker compose exec nginx nginx -t
```

For an existing database with a restricted runtime role, reapply
`infra/compose/postgres/runtime-grants.sql` as the database owner after migration
and before starting API/worker. The new receipt table requires the existing
runtime grant procedure; migration alone does not grant access to it. Follow the
[persistence permissions instructions](persistence.md).

## Monitoring

`GET /api/monitoring?window=1h` returns a bounded health summary and recent metrics
for registered servers. Windows are `1h`, `6h` and `24h`. The backend uses fixed
VictoriaMetrics queries against private TTCP-011 data; browser requests cannot
provide a query or upstream address. PostgreSQL and VictoriaMetrics have no new
public ports or browser access.

The API joins the private metrics network and uses the existing Python runtime
with HTTPX for bounded HTTP reads. The query deadline defaults to three seconds;
each response is limited to 2,000,000 bytes. Windows use 60, 300 and 600 second
resolutions, respectively, with at most 145 historical points per server.

Control Plane dependency health, collector health, managed-node collection and
TrustTunnel service/process health are distinct. Server management reachability
retains its existing meaning. CPU, memory, load, disk and network history help
inspect recent activity. CPU averages across cores, disk shows the highest usage
among selected persistent filesystems, and network rates use a five-minute window.
Source timestamps determine freshness: a sample older
than 90 seconds is stale. Missing, failed or stale observations remain explicit
and never imply a healthy server or zero resource usage. A disabled server is not
an active collection target. Metrics failure does not mutate server state, enqueue
jobs or affect existing VPN traffic.

## Audit and notifications

`GET /api/audit` returns newest-first audit events with `offset`/`limit`, an exact
total and filters for `action`, `actor_type`, `actor_id`, `target_type`, `target_id`,
`result`, `request_id`, `since` and `until`. IDs are UUIDs; time bounds must include
a timezone. Page size is 1–100 and offset is 0–100000. Responses expose actor,
operation, target, result, time and correlation ID. Raw audit metadata is omitted,
including historical fields written by earlier versions.

`GET /api/notifications` returns a similarly bounded, newest-first feed with
`unread_only`, total and unread count. Notifications use fixed safe text projected
from durable audit events: denied administrative sign-in/authorization, denied
invitation exchange, management credential rotation, user expiration/disable,
device revocation, terminal job success/failure, and explicit cancellation
requests. A cancellation request can precede the job's actual final state;
intermediate retries create no terminal failure notification. Existing matching
audit history appears without an import or GET-side writes.

`PUT /api/notifications/{uuid}/read` accepts `{"read": true}` or `{"read": false}`.
It requires the server-side administrator role, changes only the current account's
receipt, and is idempotent. Other accounts keep their own unread state. Viewer may
inspect all three pages but cannot mark notifications or perform privileged
mutations. There is no delete/dismiss API or shared acknowledgement that hides an
incident from another account.

All endpoints use current authenticated Admin Web sessions, same-origin browser
request protections and no-store responses. NGINX permits only the exact new
GET endpoints and the UUID read-state PUT endpoint on the admin host. It rejects
unsupported methods and unknown subroutes before the API; the VPN host exposes
none of them. Presentation remains in components/styles, with contracts, HTTP and
state orchestration in the existing Figma-replaceable layers.

The feed uses available operational/security event sources. Automatic offline,
service-down, resource-threshold, certificate, update and backup alerts require
future event producers. They are not inferred or persisted during a Monitoring
read. Telegram, server deploy/update/restart/uninstall and configuration revisions
are outside TTCP-016. See [ADR-0019](adr/0019-admin-visibility.md).

## Manual acceptance

1. Migrate and rebuild the runtime. Sign in as admin on the admin host. Open
   Monitoring and choose each window. Check the dependency summary, source times,
   server collection/service status and recent metrics against the private
   TTCP-011 stack. Disable a server through the existing Servers flow and verify
   Monitoring identifies it as disabled rather than healthy.
2. With an enabled bootstrapped test node, stop its exporter or block its existing
   management connection using the test environment. After a scrape interval and
   freshness expiry, verify failure/staleness and missing metrics are explicit.
   Restore the test node. Temporarily stop VictoriaMetrics in the test runtime;
   verify Monitoring reports unavailable observations and recovers after restart.
   Already issued VPN configurations must continue to work.
3. Open Audit. Filter an action and result, then actor/target or correlation ID;
   choose a timezone-aware date range, navigate pages and clear filters. Compare
   results with actions performed on disposable resources. An invalid range or
   page size must show an error. Raw metadata, SSH keys, invitation tokens,
   passwords and generated configurations must never appear.
4. On disposable resources, run a status/pre-flight job through success/failure,
   cancel a queued job, revoke a device, and attempt a denied sign-in. Refresh
   Notifications and verify safe, meaningful events. Mark one read, reload and
   confirm persistence; switch to unread-only and confirm the read entry is hidden.
   Clear Unread only to view read entries and mark it unread again. Repeating
   the same read-state PUT must not duplicate notifications or receipts. Sign in
   with another admin account and confirm its unread state is independent.
5. Sign in as viewer. Read Monitoring, Audit and Notifications. No read-state or
   privileged mutation controls should appear. A direct notification read-state
   PUT using the viewer cookie and required browser header must return 403.
   Unauthenticated requests return 401. Revoke/expire an open session and refresh;
   protected content must clear and require sign-in.
6. On the VPN host, request `/api/monitoring`, `/api/audit`, `/api/notifications`
   and a notification read-state endpoint with supplied admin credentials: each
   returns 404. On the admin host, new unsupported methods return 403 and paths
   such as `/api/monitoring/query` and `/api/notifications/{uuid}/delete` return
   404. Confirm no credentialed CORS and no generic VictoriaMetrics proxy.
   Repeat against production HTTPS.
7. At desktop and narrow mobile widths, inspect loading, empty and failed states;
   use keyboard navigation for filters, pagination and notification actions.
   Check that errors contain no upstream addresses or submitted secrets and that
   browser localStorage/sessionStorage remain empty.

Automated real-edge tests use `TTCP_TEST_NGINX`; disposable PostgreSQL tests use
`TTCP_TEST_POSTGRES=1` and `TTCP_POSTGRES_*`, as documented in [Admin Web
validation](admin-web.md#application-boundaries-and-validation). They do not replace
live-node and full deployment acceptance above.

## Local validation record — 2026-10-02

Checks ran from this Windows checkout with its `.venv`, bundled Node, a fresh
loopback-only PostgreSQL instance on port 55416, and the existing local NGINX
executable. PostgreSQL tests used random disposable schemas and
`TTCP_TEST_POSTGRES=1`/`TTCP_POSTGRES_*`; edge tests used `TTCP_TEST_NGINX` and
temporary certificates with a stub upstream.

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.tools/pytest016-full --tb=short
.venv/Scripts/python.exe -m pytest -q tests/test_admin_monitoring.py --basetemp=.tools/pytest016-monitoring-final --tb=short
.venv/Scripts/python.exe -m pytest -q tests/test_admin_web.py --basetemp=.tools/pytest016-admin-rerun --tb=short
.venv/Scripts/python.exe -m pytest -q tests/test_client_edge.py --basetemp=.tools/pytest016-edge --tb=short
.venv/Scripts/python.exe -m ruff check apps tests migrations
.venv/Scripts/python.exe -m ruff format --check apps tests migrations
.venv/Scripts/python.exe -m compileall -q apps migrations tests
git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check
```

Full suite: **266 passed, 5 skipped**. Final focused monitoring checks after its
history-gap/cookie updates: **21 passed**. Focused Admin Web cookie and real-edge
checks: **7 passed each**. The five full-suite skips cover unavailable real Redis
and platform-specific execution/bootstrap checks. Ruff, formatting, compilation
and whitespace checks passed.

From `apps/admin-web`, direct Node entrypoints were used for the repository-defined
checks because Windows sandbox restrictions affected esbuild parent-directory
reads:

```sh
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js .
node node_modules/prettier/bin/prettier.cjs --check .
node node_modules/vitest/vitest.mjs run
node node_modules/vite/bin/vite.js build
```

All checks passed, including **16 Admin Web tests** and the production bundle build.
The root-level `node .tools/admin-visibility-qa.cjs` check launched headless Edge
against a loopback Vite preview with synthetic HTTP responses. It exercised the
three new pages at desktop and 390 px widths, chart gaps/isolated samples, retained
audit filters, pagination, keyboard table scrolling, expanded event context,
read/unread filtering, viewer controls, failed-request recovery and session expiry.
No JavaScript errors, page overflow or browser-storage data appeared. Screenshots
were inspected; this check found and verified the sparse-chart and retained-filter
fixes. The harness and screenshots remain ignored local artifacts. The local
PostgreSQL and Vite helpers were stopped after validation.

```powershell
.tools/docker-compose.exe -f infra/compose/compose.yaml config --quiet
.tools/docker-compose.exe -f infra/compose/compose.yaml -f infra/compose/compose.production.yaml config --quiet
```

Both Compose validations passed with synthetic environment values. Docker Engine
and a live VictoriaMetrics/managed-node stack were unavailable, so image builds,
full Compose runtime and live query/SSH acceptance remain unverified. Metrics API
tests use synthetic VictoriaMetrics responses; browser checks use a synthetic API.
No production deployment, commits or pushes were performed.
