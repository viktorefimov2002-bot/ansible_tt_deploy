# ADR-0017: Client Portal cookie sessions

- Status: Proposed; implemented in the explicitly requested TTCP-014 follow-up
- Date: 2026-10-01
- Sources: architecture sections 15, 23, 27; TTCP-013/014; ADR-0012

## Decision

Restore Client Portal authentication after refresh using an opaque, host-only
Secure/HttpOnly/SameSite=Strict cookie scoped to `/api/client`. PostgreSQL keeps
only the existing token digest, absolute expiry and revocation state; no migration
or refresh-token mechanism is needed. Browser exchange explicitly selects cookie
mode and returns expiry metadata without the token. Retain TTCP-013 bearer exchange
for CLI compatibility. Admin authentication remains bearer-only as in ADR-0012.

Every cookie-authenticated API call and cookie-mode exchange requires the portal
custom header. Reject cross-site/same-site fetch metadata and mismatched Origin
host/port where supplied. There is no credentialed CORS policy. Logout revokes the
DB session and clears the cookie; invalid/expired/revoked sessions fail closed.
The frontend's HTTP/session-state layers own restoration. Configuration secrets
remain ephemeral; visual layout and Figma replacement boundaries are unchanged.

The direct NGINX edge rate-limits exchange by TCP peer, independent of client-sent
forwarded headers. The existing Redis socket-peer counter remains an aggregate
second layer. A future trusted ingress chain requires explicit allowlisted proxies.

## Consequences

HTTPS is required in production. Browser Secure-cookie support on HTTP loopback
varies. Refresh does not extend absolute session expiry, and copying browser storage
is unnecessary. Cookie CSRF protection is now part of the client API contract;
admin transport/security is unchanged. Details and regressions are in
[Client Portal](../client-portal.md) and [client API](../client-api.md).

References: [OWASP custom-header CSRF guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html#employing-custom-request-headers-for-ajaxapi),
[NGINX rate limiting](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html).
