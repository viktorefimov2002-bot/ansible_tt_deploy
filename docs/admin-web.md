# TTCP-015 — Admin Web core

TTCP-020 adds **Backups**: latest outcome, history, verified snapshot age, job links
and an admin-only fixed **Run backup now** action. History refreshes every five seconds.
Viewers inspect only. Failed/unknown outcomes stay explicit; there is no restore UI.
See [remote backup configuration and recovery](backups.md).

TTCP-017 adds server workload lifecycle controls and version/state/job links.
See [managed lifecycle](server-lifecycle.md) for requirements and recovery.

TTCP-016 adds [Monitoring, Audit and Notifications](admin-visibility.md), with
authenticated backend summaries, filtered pagination and durable per-admin read state.

## Local runtime

After configuring `infra/compose/.env` using the existing runtime instructions:

```sh
cd infra/compose
docker compose run --rm --build migrate
docker compose up -d --build api worker nginx
docker compose exec nginx nginx -t
```

- Admin Web: `http://admin.localhost:8080/`
- Client Portal: `http://vpn.localhost:8080/`
- NGINX health probe: `http://127.0.0.1:8080/healthz`

Use [offline admin enrollment](authentication.md) to create your account; no
default passwords or TOTP seeds are provided. Viewer enrollment remains trusted
operator maintenance, as in TTCP-006. Backend permissions are rechecked on every
request. Browser sessions are host-only Secure/HttpOnly/SameSite=Strict cookies;
no tokens or credentials are kept in localStorage/sessionStorage. HTTPS is required
in production. Secure-cookie support over local HTTP varies by browser; use local
HTTPS if your browser rejects it. If `.localhost` subdomains do not resolve on
your platform, map both names to `127.0.0.1` locally.

Frontend development, with the Compose edge running:

```sh
cd apps/admin-web
pnpm install --frozen-lockfile
pnpm dev
pnpm typecheck
pnpm lint
pnpm format:check
pnpm test
pnpm build
```

Open `http://admin.localhost:5174/`. Client Vite is
`http://vpn.localhost:5173/`. Use the named hosts: Vite preserves the browser Host
when proxying to the edge, which applies the same allowlists. Invite links from
Admin Vite point to the VPN edge on port 8080. Production links infer the sibling
`vpn` hostname; an optional build-time `VITE_CLIENT_PORTAL_URL` can specify the
separate client origin for another local topology. It never enables CORS or API
calls to that origin.

## Production entrypoint

Use Compose 2.24.4+ for the `!override` overlay. Set protected environment values:
`TTCP_ADMIN_HOST=admin.<domain>`, `TTCP_CLIENT_HOST=vpn.<domain>` and an absolute
`TTCP_TLS_DIR`. DNS must resolve both names to the entrypoint. The mounted directory
must contain `admin/fullchain.pem`, `admin/privkey.pem`, `client/fullchain.pem`,
`client/privkey.pem`. Keep private key permissions restricted. Provide renewed
certificates and reload NGINX as part of the existing operator TLS procedure.

```sh
cd infra/compose
docker compose -f compose.yaml -f compose.production.yaml config --quiet
docker compose -f compose.yaml -f compose.production.yaml up -d --build api worker nginx
docker compose -f compose.yaml -f compose.production.yaml exec nginx nginx -t
```

The overlay replaces development ports/mounts, publishes 80/443, redirects HTTP to
the respective HTTPS host, and renders the production template with only the two
hostname variables. Hostname validation runs before template expansion. Unknown
hosts/SNI fail closed. Static bundles have separate roots and share strict CSP,
no-store, no-referrer and same-origin resource policies. Each vhost has its own API
allowlist and sensitive-endpoint rate limit. There is no `/client/` or `/admin/`
production mount and no shared `/api/` passthrough. API/PostgreSQL/Redis stay private.
Certificate issuance/renewal and full production operations remain operator work.

## Manual acceptance

1. Open Admin Web. Sign in with password and a fresh six-digit TOTP. Refresh and
   confirm restoration without another OTP. Inspect the host-only cookie and
   absence of auth/session data in browser storage. Sign out and refresh: sign-in
   must be required again. Invalid OTP/login and server failure must show errors.
2. Confirm Dashboard totals and last observed status. Add an already bootstrapped
   server with management key and independently verified pinned host public key.
   Edit metadata, run pre-flight/status, and follow the queued/running job into
   diagnostic checks and live logs. Failed diagnostics must stay visibly failed.
3. Create a VPN user with a non-default device limit and optional expiration.
   Select all-server or selected-server access. Add a device up to the configured
   limit; the next addition must be blocked by backend quota. Provision credentials
   on two allowed servers for the same device without consuming another slot.
   Inspect pending/active/failed/revoking/revoked states and use retry as needed.
4. Create an invitation, copy its private fragment link, and open it in the Client
   Portal. Confirm the client session belongs only to the VPN host. Confirm/cancel
   revocation of an unused invitation, device, credential and enabled user. Disable
   an enabled server and verify further client provisioning/delivery is denied.
5. Inspect recent/older jobs. Cancel a queued or safely cancellable running job,
   first cancelling the confirmation dialog. Observe actual cancelled state or
   cancellation request; unsafe running jobs must have no cancellation control.
6. Sign in as viewer: inspect every page, invitation metadata, credentials and
   logs. No mutation controls should appear. Direct mutation requests using the
   viewer session and required browser header must return 403.
7. On the VPN host, request `/api/auth/me`, `/api/servers`, `/api/vpn-users` and
   `/api/jobs`: each returns 404 even with manually supplied admin credentials.
   On the admin host, request `/api/client/me`, `/api/client/exchange` and
   `/api/client/devices`: each returns 404. Unknown hosts/routes and CORS preflights
   must not expose the other application. Repeat against production HTTPS.
8. Try both desktop and narrow mobile widths and keyboard-only navigation. Check
   loading, empty and error states. Revoke/expire the session during an open log
   stream: protected content must clear and require sign-in.

## Application boundaries and validation

TTCP-018 adds [server configuration revisions](server-configuration.md) under
Servers: validated revision creation, immutable history, confirmed apply, current
state and safe rollback/failure details. Viewer has read-only history and job links.
The new configuration domain/API/state modules remain independent of presentation.

Replace `src/components/`, `src/components/ui/` and `src/styles.css` with future
Figma presentation. Keep `src/domain/` for contracts, `src/api/` for host-relative
HTTP and stream handling, and `src/state/` for authentication, polling, permissions,
selection, mutation orchestration and ephemeral invitation lifetime. UI components
consume actions/data and do not call the backend directly.

`GET /api/jobs?offset=0&limit=50` is authenticated, newest-first, and bounded to
100 rows per page. Invitation GET routes now permit viewer metadata reads;
POST/DELETE remain admin-only. No new persistence schema is introduced. Logs
repair from bounded durable history and SSE; Refresh reconnects after a stream
error/gap. Dashboard job counts explicitly cover recent jobs, not all history.

```sh
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
python -m compileall -q apps migrations tests
```

Set `TTCP_TEST_POSTGRES=1` and `TTCP_POSTGRES_*` for disposable-schema integration
tests. Set `TTCP_TEST_NGINX=/path/to/nginx` for real development and production TLS
edge tests in `tests/test_client_edge.py`. These prove denial before upstream access,
rate-limit behavior, no credentialed CORS, cookie CSRF/host isolation, viewer denials,
refresh/logout and current PostgreSQL session/role state. UI journey tests use a
fake HTTP boundary; real SSH/VPN operations need the manual steps above. See
[ADR-0018](adr/0018-admin-web-origins.md).

## Local validation record — 2026-10-01

Executed from this Windows checkout with the repository `.venv` and bundled Node.
Direct Node entrypoints were used because pnpm command execution and esbuild's
parent-directory reads encountered Windows sandbox restrictions. Tool execution
was permitted for tests/builds; no production deployment was performed.

- `.venv/Scripts/python.exe -m pytest -q --basetemp=.tools/pytest015-full --tb=short`,
  with `TTCP_TEST_POSTGRES=1`, disposable local PostgreSQL on port 55415 and
  `TTCP_TEST_NGINX` set: **239 passed, 5 skipped**. Skips cover unavailable real
  Redis and platform-specific execution/bootstrap tests. All new PostgreSQL and
  real NGINX/TLS isolation tests executed.
- `.venv/Scripts/python.exe -m ruff check apps tests migrations`,
  `-m ruff format --check apps tests migrations`, and
  `-m compileall -q apps migrations tests`: passed.
- In each web app: `node node_modules/typescript/bin/tsc --noEmit`,
  `node node_modules/eslint/bin/eslint.js .`,
  `node node_modules/prettier/bin/prettier.cjs --check .`,
  `node node_modules/vitest/vitest.mjs run`, and
  `node node_modules/vite/bin/vite.js build`: passed. **7 Admin Web tests** and
  **6 Client Portal tests**. Admin frozen-lockfile offline installation also passed;
  common dependency versions match the existing Client Portal lockfile.
- `.tools/docker-compose.exe -f infra/compose/compose.yaml config --quiet` and
  `-f infra/compose/compose.yaml -f infra/compose/compose.production.yaml config --quiet`,
  with synthetic validation environment: passed.
- `bash -n infra/nginx/15-web-origins.sh infra/compose/tests/runtime_smoke.sh`:
  passed. The origin guard accepted valid distinct hostnames and rejected a shared
  VPN hostname and a hostname containing shell punctuation.
- `node .tools/admin-web-qa.cjs`: headless Edge with synthetic HTTP responses
  rendered sign-in and all five pages at desktop and 390 px mobile widths, with
  no JavaScript errors or page overflow. Screenshots were inspected. The check
  caught and verified a fix for the mobile Jobs table's grid sizing.
- `git -c core.safecrlf=false -c core.whitespace=cr-at-eol diff --check`: passed;
  final tracked changes and new sources inspected. No generated bundles, local
  test secrets, commits or pushes were added.

Docker Engine is unavailable here, so image builds and the full Compose runtime
smoke were not executed. Real managed-node SSH, certificate renewal, native client
import and VPN traffic remain manual acceptance work. Production TLS edge behavior
was exercised with temporary test certificates and a local stub upstream.
