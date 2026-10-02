# TTCP-019 — Durable operational alerts

The worker evaluates existing TTCP-011 metrics in its TTCP-007 maintenance cycle,
at most once per 30 seconds per worker. Evaluation never runs from API GETs.
VictoriaMetrics stays private. The task specification is the TTCP-019 implementation
request; no separate task file exists. See [ADR-0022](adr/0022-durable-operational-alerts.md).

## Rules and evidence

| Type | Evidence to open | Debounce | Evidence to recover |
| --- | --- | --- | --- |
| NODE_OFFLINE | Fresh successful collector scrape, with failed node collection or node source older than 90 seconds | 60 seconds | Fresh successful node collection for 60 seconds |
| SERVICE_DOWN | Fresh successful node collection, with inactive service, missing process, or explicit failed status-helper probe | 60 seconds | Probe succeeds, service active and process running for 60 seconds |
| DISK_HIGH | Maximum usage across observed non-tmpfs/devtmpfs/squashfs/overlay filesystems at least 90% | 300 seconds | Usage at most 85% for 60 seconds |
| MEMORY_HIGH | `(1 - MemAvailable / MemTotal) * 100` at least 90% | 300 seconds | Usage at most 80% for 60 seconds |

NODE_OFFLINE means collection failed or went stale; it cannot prove the physical
host is powered off. SERVICE_DOWN includes a failing fixed helper and does not
prove external VPN connectivity. Resource rules cover exporter values actually
collected, not unobserved filesystems.

One fixed bounded instant query requires service/resource fields to come from the
same scrape as node status. Old lookback values from missing fields cannot supply
current evidence. Duplicate/ambiguous series, missing/invalid percentages, absent
initial node data, collector failure/staleness, and VictoriaMetrics failure are
unknown. A failed node scrape makes service/resource conditions unknown.

Successive distinct source timestamps must support the debounce. Repeated polling
of a source cannot advance it. Node recovery uses successful node sample timestamps;
collector timestamps support failure/staleness evidence only. Gaps longer than 90
seconds between observations or evaluations reset pending evidence, including after
a worker outage. Unknown observations and hysteresis bands reset pending evidence
while retaining an open incident. Recovery needs fresh evidence; missing metrics,
disablement and monitoring outages cannot close incidents. Short flaps produce
neither openings nor recoveries.

Only enabled nodes with the collector's `ttcp` SSH user, host pin and encrypted
management identity are evaluated. Other nodes are unknown. Service alerts are
suppressed during pending deploy/update/restart/uninstall/config apply and for
deliberately uninstalled services. Suppression resets pending evidence and preserves
active incidents; debounce starts again when suppression ends. Lifecycle/config/job
statuses are never alert inputs and existing failure notifications are not reproduced.
A sustained independent health failure after an operation can still produce an alert.

## Persistence and Admin Web

Migration `0012_operational_alerts` adds one `operational_alerts` row per server/type.
The primary key enforces one active incident slot for that pair. Rows contain an
active UUID/open time, pending direction/start, source timestamp and evaluation
timestamp. PostgreSQL server/cursor locks serialize evaluators; older in-flight
evaluations cannot overwrite newer state. State and immutable audit events commit
together; transaction failure rolls back both. No Redis business state is required.

Openings emit `alert.node_offline`, `alert.service_down`, `alert.disk_high` or
`alert.memory_high` with result `open`. Recovery uses the same action and result
`recovered`. Actor is `system`, target is the server UUID, and request/correlation
ID is the incident UUID shared by its opening/recovery. No raw labels, exceptions,
submitted names or credentials enter alert messages or metadata.

TTCP-016 projects events into fixed safe Notifications messages. Existing Admin
Web renders severity, target, correlation, time and per-account unread state. Audit
can filter alert actions/results. No public incident mutation endpoint, arbitrary
PromQL input or shared dismissal is added. Admin and viewer can read; only admin
can change their own read receipt. GETs do not evaluate or modify alert state.

Redis loss, worker recreation, feed polling and evaluator replay do not duplicate
incidents. Existing worker startup still requires configured PostgreSQL/Redis;
once running, maintenance continues through queue transport outages. Evaluation
runs between jobs, so long jobs or outages can delay detection. Gaps reset pending
evidence instead of asserting uninterrupted history.

## Deployment and recovery

Apply migrations before updating API/worker and reapply
`infra/compose/postgres/runtime-grants.sql` as schema owner for a separate runtime
role. Worker joins the existing private metrics network and receives fixed
VictoriaMetrics host/port. No port, NGINX route, service or dependency is added.
Rebuild/restart using existing deployment steps. PostgreSQL owns state; feed reads
need neither Redis delivery nor a running worker. Alert database failures log only
`alert_persistence_unavailable` and retry on a later maintenance cycle.

Prefer forward fixes. Downgrade requires stopping workers and deliberately loses
active incident/cursor state. Audit events/read receipts survive; re-upgrading can
create new incidents for still-failing conditions. Do not downgrade as an ordinary
restart/recovery procedure. No audit pruning/retention changes are introduced.

## Deferred sources

| Planned type | Missing trustworthy input |
| --- | --- |
| CERTIFICATE_EXPIRING | No collected certificate `notAfter`, identity or proof it serves VPN connections. Domain/ACME flags and versions do not establish expiry. |
| NETWORK_SATURATION | RX/TX rates lack validated usable interface capacity/provider shaping limits. Exporter speed is not a validated end-to-end capacity source. |
| UPDATE_AVAILABLE | Running/desired versions are inventory, not an authenticated release/support feed for a suitable available update. |
| BACKUP_FAILED | No implemented backup job/result source; backups are outside this task. |

Telegram is also deferred. None of these types is guessed or emitted.

## Validation and manual acceptance

`tests/test_operational_alerts.py` covers transitions, flapping, hysteresis, repeated
and older samples, worker gaps/recreation, concurrent evaluation, atomic rollback,
dependency outages, suppression, grants/constraints, fixed queries, safe projection
and admin/viewer permissions. Enable the migrated disposable PostgreSQL fixture
with `TTCP_TEST_POSTGRES=1` and documented `TTCP_POSTGRES_*` settings.

On a disposable deployed stack, stop/restore the exporter or management path and
TrustTunnel separately. Verify one opening/recovery per incident with shared
correlation and no events from short flaps. Exercise a failed helper and constrained
disk/memory workloads. Interrupt collector/VictoriaMetrics/Redis and restart workers:
incidents must stay open through unknown data, pending evidence must restart after
gaps, and openings must not duplicate. Check Audit/Notifications as admin/viewer,
including viewer read-receipt denial. Verify metrics remain private. Live Linux/SSH
and VictoriaMetrics acceptance supplements synthetic metric and PostgreSQL tests.

## Local validation record — 2026-10-02

Validation used the checkout's `.venv`, bundled Node, existing local NGINX, and
loopback PostgreSQL on port 55418 with disposable random schemas. Test environment
enabled `TTCP_TEST_POSTGRES=1`, `TTCP_POSTGRES_*` and `TTCP_TEST_NGINX`. No production
nodes or credentials were used.

```powershell
.venv/Scripts/python.exe -m pytest -q --basetemp=.tools/pytest019-completion --tb=short
.venv/Scripts/python.exe -m pytest -q tests/test_operational_alerts.py --basetemp=.tools/pytest019-sample-recovery --tb=short
.venv/Scripts/python.exe -m ruff check apps tests migrations
.venv/Scripts/python.exe -m ruff format --check apps tests migrations
$taskPythonFiles = @(rg --files apps migrations tests -g '*.py')
.venv/Scripts/python.exe -m compileall -q @taskPythonFiles
git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check HEAD
.tools/docker-compose.exe -f infra/compose/compose.yaml config --quiet
.tools/docker-compose.exe -f infra/compose/compose.yaml -f infra/compose/compose.production.yaml config --quiet
```

Full regression run: **447 passed, 5 skipped**. Final alert suite, including the
distinct-node-sample recovery regression: **40 passed**. Migration checks
include deterministic offline SQL, upgrade/downgrade/re-upgrade, model drift and
runtime grants. Skips concern real Redis and platform-specific execution/bootstrap
checks. Ruff, formatting, compilation and whitespace checks passed. Both Compose
configurations passed using synthetic environment values.

From `apps/admin-web`, repository-defined checks used direct bundled Node entrypoints:

```powershell
node node_modules/typescript/bin/tsc --noEmit
node node_modules/eslint/bin/eslint.js .
node node_modules/prettier/bin/prettier.cjs --check .
node node_modules/vitest/vitest.mjs run
node node_modules/vite/bin/vite.js build
```

All passed, including **27 Admin Web tests** and the production bundle. Vitest/build
were retried outside the Windows sandbox because esbuild requires parent-directory
reads.

Docker Engine and a live managed-node/VictoriaMetrics stack were unavailable.
Real MetricsQL evaluation, Linux/SSH signal collection and full deployment acceptance
remain unverified. Metrics-client tests use synthetic responses; Redis-loss alert
tests exercise the queue abstraction. No deployment, Telegram, backups, commit or
push was performed.
