# TTCP-021 release journey

On Linux with a local Docker daemon, Python development dependencies and pnpm:

Map `admin.localhost` and `vpn.localhost` to loopback in the hosts file for Node
HTTP requests. The CI workflow sets these aliases explicitly.

```sh
pnpm --dir e2e install --frozen-lockfile
pnpm --dir e2e exec playwright install --with-deps chromium
bash scripts/ci/run_e2e.sh
```

The mandatory CI flow builds the real static applications, applies Alembic
migrations to an empty disposable PostgreSQL instance, and runs the real API,
durable worker, Redis and production TLS NGINX configuration. Chromium visits
separate `https://admin.localhost:18443` and `https://vpn.localhost:18443` origins.
Self-signed certificates exist only inside the generated fixture. Only their
trust validation is bypassed; HTTPS cookie and host boundaries remain active.

The journey signs in with a freshly generated password/TOTP, creates a server
and VPN user with a non-default device quota, grants access, issues an invitation,
exchanges it through the Client Portal, creates a device, downloads a configuration
and checks QR/deep-link delivery, then revokes the device and verifies durable
credential revocation and denied further delivery. It verifies refresh/logout,
host-only cookie attributes, empty browser storage, quota enforcement, NGINX
route/method isolation, cross-origin/preflight denials and direct viewer mutation
denials. While provisioning, it stops the worker, restarts Redis to lose ephemeral
queue state, restarts the worker, and waits for PostgreSQL intent to recover.

All infrastructure uses a unique Compose project, generated synthetic identities,
an internal backend network and PostgreSQL/Redis tmpfs. NGINX alone also joins a
dedicated host-ingress bridge, which the runner creates and firewalls before
starting services. The firewall blocks new container connections outside that
bridge and to the host, permits established replies, and disables masquerading
and IPv6. Only the HTTPS edge is published, on loopback. This requires Linux
iptables and permission to install/remove rules on the dedicated test bridge.
The runner verifies both published HTTPS origins with the generated certificates,
actual SNI/Host names and static/API isolation checks before launching Chromium.
The runner clears inherited deployment/Compose/Docker
environment settings, uses an explicit local Docker socket and a generated env
file, and removes only its own containers and temporary fixture. Browser network
requests are restricted to the two local origins. Traces, videos, screenshots
and fixture/configuration bodies are excluded from CI artifacts to avoid making
test credentials a bad logging precedent.
Diagnostics print only allowlisted stages and elapsed milliseconds. Raw browser
errors (including URLs, fill values and DOM excerpts) are replaced with a safe
stage failure. `PLAYWRIGHT_NO_COPY_PROMPT=1` suppresses the locked runner's automatic
DOM snapshot; any remaining failure context contains safe errors/source only and
is removed with the fixture. No failure artifacts are uploaded. Response/download
waits are paired with their actions to avoid dangling rejected promises. Browser
actions/navigation are bounded at 10/15 seconds, while the journey retains its
120-second deadline. Cleanup cannot overwrite the primary stage failure.

Dropdowns use semantic combobox selectors with label prefixes. Their wrapping
labels include option text, so exact `getByLabel` matches can silently wait for
the full test deadline. This affected Access mode and the Client platform/device/
server choices in run `37795769907`. No UI or product behavior was changed.

The sole fixture is the managed-node execution port: it accepts only credential
create/revoke for `managed-node.invalid` and `vpn-node.invalid`, executes no shell,
SSH or network operation, and returns disposable configuration text. All API,
authorization, encryption-at-rest, durable job/audit, Redis and browser behavior
around that port remains real. This does not prove TrustTunnel accepts the TOML,
VPN packets pass, or managed-node deployment/update/rollback works. Those and real
monitoring/S3 operation require the separate staging acceptance checklist. The
test fails if dependencies are unavailable; it has no skip or mock API fallback.
