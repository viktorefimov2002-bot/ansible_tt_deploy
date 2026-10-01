# TTCP-014 — Client Portal

## Run and deploy

Client URL through Compose/NGINX: `http://vpn.localhost:8080/`.
Frontend-only development: `http://vpn.localhost:5173/`.
TTCP-015 moves the client bundle to a separate host root. Admin Web runs at
`http://admin.localhost:8080/`; see [deployment and acceptance](admin-web.md).

From `apps/client-web`:

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

Vite proxies `/api` to the local entrypoint on port 8080. The development UI
requires a running API, PostgreSQL, Redis and credential worker for a real journey.
The pinned pnpm version is in package.json. NGINX's multi-stage Dockerfile builds
static assets; no Node process runs in production. From `infra/compose`, after
configuring the existing protected environment:

```sh
docker compose run --rm migrate
docker compose up -d --build api worker nginx
```

Migration `0008_client_config` adds nullable encrypted generator output. Reinstall
the reviewed `scripts/node_credential.py` on managed nodes using the existing
bootstrap procedure before provisioning; an old helper fails closed on the new
payload. Existing active credentials without configuration can be provisioned
again to generate their configuration without allocating another device.
Do not run live-node bootstrap automatically as part of this frontend deployment.

The existing `/opt/trusttunnel` installation must contain `trusttunnel_endpoint`,
`vpn.toml`, `hosts.toml` and credentials readable by the privileged helper.
Generation invokes exactly the existing official CLI with `-c`, `-a`,
`--format toml` / `--format deeplink`, and `--name TrustTunnel`.
See the [official export instructions](https://github.com/TrustTunnel/TrustTunnel#export-client-configuration).
The endpoint determines TLS/connection settings from its actual files. The portal
never renders a made-up configuration from credentials and server metadata.
It does not enable subscriptions or generate new random-prefix rules.

## Manual acceptance journey

1. Log in using [Admin Web](admin-web.md) or existing admin API authentication.
   Prepare an enabled managed server and a VPN user with access to that server.
2. `POST /api/vpn-users/{user_id}/invitations` with the admin bearer header and `{}`.
   Use the one-time response token in
   `http://vpn.localhost:8080/#invite=<token>`. Share privately. Fragment tokens
   are not sent to NGINX. Query `?invite=` is also accepted for compatibility;
   prefer fragments. Client static access logging is disabled.
3. Open the link. The URL is cleaned before exchange. Verify profile, expiration
   and the backend-provided device quota. Refresh the page: the same profile and
   device list must return without another invitation/exchange.
4. Add a named device (optionally choose its platform), select an accessible
   server, and click **Настроить подключение**.
5. Observe queued/pending and running states. Worker success produces **Готово к
   подключению**. A failed job offers retry; another server on the same device
   consumes no additional device quota.
6. Click **Получить конфигурацию**, then **Открыть в TrustTunnel** on a device with
   TrustTunnel installed. In **Другие способы подключения**, scan the locally
   generated QR, download TOML, or copy the exact TOML. Import it and verify VPN
   traffic against the managed test endpoint.
7. Cancel device revocation once, then confirm it. Verify the configuration
   disappears and further delivery fails. Also test disabled/expired users,
   disabled servers, removed server access and logout. Refresh after logout: the
   login/invitation screen must remain visible.

The browser session lives in a Secure/HttpOnly/SameSite=Strict host-only cookie
scoped to `/api/client`. Normal page refresh restores the existing valid session
without reusing the invitation. No session token is returned to JavaScript or
written to localStorage/sessionStorage. Logout or absolute expiry requires a new
invitation. Configuration material remains memory-only and is fetched again after
refresh. Clipboard copy needs a secure browser context (HTTPS, or local
loopback during development). Production client hosting must use HTTPS. Local Secure-cookie testing over HTTP
loopback is browser-dependent; use HTTPS for full cross-browser validation (Safari
may reject Secure cookies on HTTP loopback). Previously
exported files cannot be erased by the portal; remote credential revoke invalidates
VPN access after its durable job is applied.

## Contract and security

- `GET /api/client/devices/{id}/provisioning`: accessible Device × Server states
  `pending`, `running`, `failed`, `ready`, `revoked`, with no admin job logs or secrets.
- `POST /api/client/devices/{id}/configurations/{server_id}`: exact generated
  `{toml, deep_link}`. `?format=toml` returns a downloadable TOML response.
- Delivery is repeatable while access remains valid; it does not consume the
  legacy admin one-time raw credential handoff.
- Every delivery checks session, enabled/unexpired user, device ownership/status,
  enabled server/access and active credential in PostgreSQL. Responses, including
  errors, have `Cache-Control: no-store`. Delivery is audited without secret content.
- The worker encrypts generator output with the existing application encryption
  key. No configuration enters Redis, audit details or normal execution events.
  Ansible captures helper output with `no_log`, copies it into the adapter's
  private temporary directory, and the adapter deletes that directory on exit.
- UI tokens/configurations never enter localStorage/sessionStorage, URLs after
  exchange, application logs, third-party QR services or service-worker caches.
  Polling revalidates access and clears delivery on failures; selection and logout
  also clear delivery. In-flight results from old selections are discarded.

## Invitation exchange edge limit

The direct NGINX edge uses `$binary_remote_addr` (TCP socket peer), 5 requests/minute
and a burst of 5 on the exact `/api/client/exchange` route. Excess requests return
429 with `Cache-Control: no-store`. Incoming `Forwarded`, `X-Forwarded-For`,
`X-Real-IP` and `X-Forwarded-Host` cannot alter the limiter's key. Forwarded client
metadata is overwritten/removed before proxying the exchange to the API.
There is no blanket `set_real_ip_from` or header-based real-IP trust.
If placing a CDN/load balancer before this edge later, configure explicit trusted
proxy CIDRs and prevent direct bypass before enabling a real-IP header; the current
configuration intentionally treats that proxy as the TCP peer.

Run the real NGINX regression with:

```sh
TTCP_TEST_NGINX=/path/to/nginx python -m pytest -q tests/test_client_edge.py
```

The test uses the checked-in edge configuration with temporary local listen/upstream
ports, changing forwarded headers, and two actual socket source addresses. It checks
429/no-store, per-client isolation and independence from admin/session routes.

## Frontend boundaries for the future Figma design

Replace `src/components/PortalView.tsx`, presentation components including
`ConfigurationDelivery.tsx` and `ConfirmRevoke.tsx`, `src/components/ui/`, and
`src/styles.css` (central visual tokens). They use the React/TypeScript/Vite and
shadcn component baseline (Radix primitives, CVA, components.json, Tailwind).
Keep `src/api/` (HTTP/auth contract), `src/domain/` (types/state vocabulary) and
`src/state/` (orchestration, polling, selection and secret lifetime). The visual
layer accepts data/actions from the state layer and can be replaced independently.

## Validation limits

Automated UI journey tests use the real portal/controller with a fake HTTP
boundary; backend integration tests use real PostgreSQL migrations/domain/worker
and an execution port returning synthetic official-generator artifacts. Node
helper tests check delegation to both official CLI formats and failure handling.
A real SSH node, native app import and VPN traffic require the manual journey above.
