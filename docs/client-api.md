# TTCP-013 — Client authentication and self-service API

Run migration `0007_client_auth` and reapply `infra/compose/postgres/runtime-grants.sql`
before starting the updated API. Invitation and client session rows are durable in
PostgreSQL. Only SHA-256 token digests are stored. Redis holds only a one-minute
exchange attempt counter keyed by a hash of the socket peer address; exchange fails
closed if Redis is unavailable. The API allows 30 exchange attempts per peer per
minute. Place the API behind HTTPS and add a trusted-edge per-client rate limit when
multiple clients share the API's proxy peer address. Do not log request bodies or
Authorization headers at that edge.

An admin bearer session can `POST /api/vpn-users/{user_id}/invitations` with optional
`{"lifetime_seconds": 86400}` (300–604800). The response contains the token once;
`GET` on the collection or `/{invitation_id}` returns metadata without it. `DELETE`
on `/{invitation_id}` revokes an invitation. Viewer sessions cannot manage invitations.

`POST /api/client/exchange` accepts `{"token": "..."}` and returns a client bearer
`access_token` and `expires_at`. Used, revoked, expired and unknown invitations all
return the same 401. Send that token only in `Authorization: Bearer` headers. Client
tokens cannot authenticate to admin routes, and admin tokens cannot authenticate to
client routes. `POST /api/client/logout` revokes the current client session. Disabling
or expiring a VPN user revokes client sessions; every request also checks current
user status and expiry. Authentication and invitation responses carry
`Cache-Control: no-store`.

Client routes are scoped to the bearer session's user ID:

| Method and path | Result |
| --- | --- |
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
