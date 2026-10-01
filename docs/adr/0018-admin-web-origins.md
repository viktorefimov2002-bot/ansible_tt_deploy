# ADR-0018: Admin Web browser transport and separate origins

- Status: Proposed; implemented for the explicit TTCP-015 request
- Date: 2026-10-01
- Sources: architecture sections 4, 21–23, 27; TTCP-015; ADR-0012 and ADR-0017

## Decision

Build independent static React/TypeScript/Vite applications at the root of
`admin.<domain>` and `vpn.<domain>`. The private FastAPI service is shared. Each
NGINX virtual host has an explicit route and method allowlist; unknown APIs are
denied before SPA fallback. Deployment uses distinct HTTPS virtual hosts and
separate certificate paths, with an HTTP redirect and an unknown-SNI rejection.
Development uses `admin.localhost` and `vpn.localhost` on the same loopback port.

Extend ADR-0012's opaque PostgreSQL sessions with opt-in Admin Web cookie transport:
`__Host-ttcp_admin`, Secure, HttpOnly, SameSite=Strict, host-only, Path=/.
The browser-enforced `__Host-` prefix prevents a compromised sibling host from
injecting a parent-domain admin cookie.
Cookie login and every cookie-authenticated request require `X-TTCP-Admin: web`,
same-origin fetch metadata and matching Origin host/port when supplied. No CORS
permission is granted. CLI bearer login remains the default; explicit bearer
headers never fall back to a cookie. SSE checks the same resolved token throughout
the stream. Expiry/revocation clears the cookie; logout revokes the durable session.
This supersedes only ADR-0012's browser transport restriction and ADR-0017's
statement that admin authentication remains bearer-only.

Viewer may read invitation metadata (never tokens), jobs and other managed
resources. Mutations continue to require server-side Administrator authorization.
Ending one's own auth session is bookkeeping, as already established by ADR-0012.

## Alternatives and consequences

An in-memory bearer would require a new password/TOTP login after every reload.
Browser storage would expose session tokens to JavaScript and is unnecessary.
Separate production paths on one host contradict the required security boundary.

No schema, refresh-token or Redis session changes are needed. The runtime key and
PostgreSQL remain authoritative. Existing `/client/` links must migrate to the VPN
host root. Production requires operator-managed DNS and TLS certificates; the
overlay mounts them read-only. ACME renewal is an operator deployment concern.
Logs, invitation tokens and SSH inputs stay out of browser storage. Invitation
links use fragments and are cleared on selection/navigation/logout/role changes.
NGINX's route policy must be extended explicitly when future APIs are introduced.

The old development edge exposed a broad shared `/api/` surface. TTCP-015 replaces
that surface rather than preserving it. Internal health endpoints remain on the
private API; the public web hosts only expose their required application routes.
