# 0012 — Administrative authentication and sessions

Status: Proposed (implementation choice for TTCP-006; owner acceptance pending)

## Context

Architecture sections 21–23 require username/password plus TOTP, admin/viewer RBAC,
audit, and PostgreSQL authentication state. TTCP-005 supplies identities and audit
records but leaves the session transport, password algorithm and MFA key storage open.
TTCP-006 implements the minimal administration foundation without identity CRUD or UI.

## Decision implemented

Use Argon2id (19 MiB, two iterations, parallelism one), encrypted TOTP seeds with
Fernet, and 256-bit opaque bearer tokens. PostgreSQL stores only SHA-256 session
token digests, with an absolute expiry and explicit revocation. Authentication checks
the current account role/enabled state on every request. No JWT claims or Redis
authentication state are authoritative. TOTP uses six digits, 30-second steps and
a ±1-step window; a locked account row serializes consumption and prevents replay.

Bearer tokens are accepted only in Authorization headers. No cookies, query-token
support, refresh tokens or persistent browser storage are introduced. A future
browser integration must revisit transport/storage and CSRF protection if adopting
cookies. The current API is usable by an operator client over HTTPS.

The initial administrator is enrolled through an interactive offline command,
using an operator-generated authenticator seed and confirmed OTP. Bootstrap is
serialized and refuses any existing admin-role account, including disabled accounts.
Passwords and seeds are never accepted in argv. No default credentials or public
registration/reset endpoint exists.

The TOTP encryption key is supplied to API/bootstrap via protected environment,
consistent with the current runtime configuration. It is not a signing key and
does not need to be given to the worker. Losing it prevents new logins; retain an
encrypted backup separately from the database. Key rotation/recovery is an explicit
operator maintenance task; automated rotation and MFA recovery are outside this task.

## Alternatives and consequences

JWT would complicate immediate revocation and role changes. Cookie sessions would
require a browser origin/CSRF contract before the frontend exists. An external IdP
is outside scope. Fernet adds one runtime secret and a cryptography dependency;
PyOTP and argon2-cffi avoid implementing security algorithms locally.

Five failed password/OTP attempts lock an identity for five minutes. NGINX limits
login requests per source address. The account lockout is shared across API instances;
it intentionally trades some availability for resistance to password/OTP guessing.
The API remains private behind NGINX; authentication requires production TLS at the
entrypoint. An unset encryption key leaves health probes operational and auth closed
with 503, preserving the existing development runtime.

Viewer may read its own identity and end its own session. Revoking another session
requires admin. Login/logout/session lifecycle writes are authentication bookkeeping,
not permission for viewer to mutate managed business resources.

Audit is committed with session issuance/revocation; denied login/authorization
events also persist. Unknown callers use a system actor without recording submitted
identity strings, passwords, OTPs or tokens. Each HTTP request receives a generated
correlation ID. Audit storage failure fails the operation closed.

Expired/revoked session rows need periodic operator cleanup until a retention job is
implemented. Disable/revoke credentials via trusted maintenance and revoke sessions
in the same transaction when changing passwords or MFA. No account management API
or recovery workflow is part of TTCP-006.

## References

- [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [RFC 6238](https://www.rfc-editor.org/rfc/rfc6238)
- [Operator/API instructions](../authentication.md)
