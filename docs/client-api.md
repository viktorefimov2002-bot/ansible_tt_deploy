# TTCP-013 — Client authentication and self-service API

Run migration `0007_client_auth` and reapply `infra/compose/postgres/runtime-grants.sql`
before starting the updated API. Invitation and client session rows are durable in
PostgreSQL. Only SHA-256 token digests are stored. Redis holds only a one-minute
exchange attempt counter keyed by a hash of the socket peer address; exchange fails
closed if Redis is unavailable. The API allows 30 exchange attempts per peer per
minute. NGINX additionally limits `/api/client/exchange` to 5 requests/minute
per TCP edge client, with a burst of 5 and HTTP 429/no-store for excess requests.
It never keys this bucket from arbitrary forwarded headers. The API socket-peer
counter remains an aggregate defense when clients share a proxy peer. Place the
public edge behind HTTPS. Do not log request bodies or
Authorization headers at that edge.

An admin bearer session can `POST /api/vpn-users/{user_id}/invitations` with optional
`{"lifetime_seconds": 86400}` (300–604800). The response contains the token once;
`GET` on the collection or `/{invitation_id}` returns metadata without it. `DELETE`
on `/{invitation_id}` revokes an invitation. TTCP-015 permits viewer GET metadata
reads; viewer POST/DELETE remain forbidden. Admin Web cookies can also use these
admin-origin routes with the required browser header.

`POST /api/client/exchange` accepts `{"token": "..."}` and returns a client bearer
`access_token` and `expires_at` for the existing bearer/CLI contract. The portal
sends `session_mode: "cookie"` and `X-TTCP-Client: portal`; that response returns only
`expires_at` and `token_type: "cookie"` and sets a host-only `__Secure-ttcp_client`
cookie with Secure, HttpOnly, SameSite=Strict, Path=/api/client, and the session's
absolute expiry/Max-Age. No session token is exposed to browser JavaScript.
`GET /api/client/session` authenticates that cookie and returns only `expires_at`
to restore the portal after page refresh. Used, revoked, expired and unknown invitations all
return the same 401. Send bearer tokens only in `Authorization: Bearer` headers.
For cookie requests, every portal API call supplies `X-TTCP-Client: portal`. The API
rejects missing markers, cross-site/same-site fetch metadata, and mismatched Origin
hosts (including port). No cross-origin credentialed CORS is enabled. NGINX preserves
the original Host authority for this check; Origin checking ignores forwarded
headers. Explicit Authorization always takes precedence without cookie fallback.
Client
tokens cannot authenticate to admin routes, and admin tokens cannot authenticate to
client routes. Cookie scope does not include admin endpoints, which never accept
client cookies. `POST /api/client/logout` revokes the current client session and
expires the client cookie. An invalid/expired/revoked session also clears the stale
cookie when an authenticated client route returns 401. Disabling
or expiring a VPN user revokes client sessions; every request also checks current
user status and expiry. Authentication and invitation responses carry
`Cache-Control: no-store`.

Client routes are scoped to the authenticated client session's user ID:

| Method and path | Result |
| --- | --- |
| `GET /api/client/session` | Current session expiry; cookie restoration |
| `GET /api/client/me` | Own profile, device limit and enabled device count |
| `GET /api/client/servers` | Enabled accessible server metadata |
| `GET /api/client/devices` | Own devices |
| `POST /api/client/devices` | Add an owned device, enforcing device_limit |
| `DELETE /api/client/devices/{device_id}` | Revoke an owned device and its credentials |
| `GET /api/client/devices/{device_id}/credentials` | Own Device × Server credential state |
| `POST /api/client/devices/{device_id}/credentials/{server_id}` | Request TTCP-012 durable credential job; returns only job ID and status |

Clients cannot use admin job, credential secret handoff, VPN user mutation or server
management endpoints. Configuration rendering, QR and `tt://` delivery are implemented by
[TTCP-014 Client Portal](client-portal.md).
