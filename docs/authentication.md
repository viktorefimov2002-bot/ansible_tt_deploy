# TTCP-006 — Administrative authentication

Implemented: password + TOTP login, expiring/revocable PostgreSQL sessions, reusable
FastAPI request context and admin authorization, initial admin enrollment, audit.
Identity CRUD, recovery/reset, client authentication and frontend UI are out of scope.
The supplied TTCP-006 request is the task scope; the architecture additionally requires
TOTP, which is included. See [session decision](adr/0012-admin-auth-sessions.md).

## Setup

1. Install the updated lock file and run `python -m alembic upgrade head` as the
   database owner. Reapply `infra/compose/postgres/runtime-grants.sql` for the runtime
   role; the new `admin_sessions` table needs explicit grants.
2. Generate a Fernet key in a trusted secret-management environment using
   `Fernet.generate_key()` from `cryptography.fernet`. Store it in the protected
   Compose `.env` as `TTCP_AUTH_ENCRYPTION_KEY`. Never place it in Git or shell argv.
   Back up this key separately from the database. It encrypts TOTP seeds at rest.
3. Prepare a random 32-character uppercase base32 authenticator seed (for example,
   with `pyotp.random_base32()` inside a trusted password manager/enrollment tool).
   Enroll it in the operator's authenticator with SHA1, six digits, 30-second period.
   Do not use an online QR generation service or put the seed in command history.
4. With the runtime settings present, run `python -m apps.api.bootstrap_admin` in an
   interactive terminal. Enter the username, password twice, seed and current OTP at
   the hidden prompts. Bootstrap must use the database owner because it explicitly
   locks the identity table. It fails if any admin-role account already exists.
5. Wait for the next OTP, then start/recreate API. Do not send credentials to a remote
   HTTP endpoint: the existing Compose listener is loopback development HTTP, not a
   production TLS terminator. Configure HTTPS before remote use.

From `infra/compose`, migrations use `docker compose run --rm --build migrate`.
For interactive enrollment with owner credentials:

```sh
docker compose run --rm -it -e TTCP_AUTH_ENCRYPTION_KEY \
  migrate python -m apps.api.bootstrap_admin
docker compose up -d --build --wait api
```

The `-e NAME` form copies the protected key from the calling environment without putting
it in argv. Set it there first; Compose `.env` alone does not export shell variables.
Enrollment requires only PostgreSQL and the encryption key, not Redis configuration.
Bootstrap failure never prints raw exception/input data.
The command never overwrites credentials and is not a password reset mechanism.
Existing TTCP-005 accounts without an enrolled seed cannot log in. Viewer integration
uses the existing `Admin` model (`role='viewer'`, enabled, Argon2id hash, encrypted seed);
no user-management endpoint is added. Tests exercise actual persisted viewer accounts.

## HTTP contract

All paths below are under `/api/auth`. Auth responses use `Cache-Control: no-store`
and a server-generated `X-Request-ID`. Clients must avoid logging bodies/headers.

| Method/path | Authentication / permission | Result |
| --- | --- | --- |
| POST `/login` | Public; JSON `username`, `password`, `totp_code` | 200: `access_token`, `token_type=bearer`, `expires_at` |
| GET `/me` | Admin or viewer bearer session | ID, username, current role, session ID |
| POST `/logout` | Admin or viewer; own session | 204, session revoked |
| DELETE `/sessions/{uuid}` | Admin only | 204 (also for already revoked), 404 if absent |

Send `Authorization: Bearer <access_token>` on protected calls. Never put tokens in
URLs/cookies or browser localStorage. No refresh flow exists; authenticate again after
expiry. `TTCP_AUTH_SESSION_SECONDS` defaults to 28800 (8 hours), range 300–86400.
Invalid credentials return generic 401; missing/invalid/expired/revoked sessions return
401, viewer privileged mutations return 403, malformed input returns redacted 422,
and unavailable authentication storage/key returns 503. Account lockout uses the same
generic 401. NGINX rate limits may return 429. Health/readiness contracts remain public.

Future read handlers use `Authenticated`; privileged handlers use `Administrator`
from `apps.api.auth`. These resolve `Principal` from PostgreSQL and also place it in
`request.state.principal`. Never accept actor/role claims from request bodies. For new
business routers, apply these dependencies at the router or handler boundary and
test negative permissions. Session revocation is the privileged operation implemented
here; no placeholder server/device mutations are exposed.

## Maintenance and validation

Use trusted DB maintenance to expire/remove old session rows; keep audit records.
When disabling an account, access immediately fails on subsequent requests. Role
changes take effect on subsequent checks. When changing a password/MFA seed, revoke
that identity's sessions in the same transaction. Key replacement requires decrypting
and re-encrypting seeds before switching API configuration; no automatic rotation is
provided. Key loss requires explicit offline reenrollment and session revocation.

```sh
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
python -m compileall -q apps migrations tests
bash infra/compose/tests/runtime_smoke.sh
```

Set `TTCP_TEST_POSTGRES=1` and `TTCP_POSTGRES_*` for a disposable PostgreSQL database
to include real migrations, credentials, MFA replay/concurrency, bootstrap, role
changes, expiry/revocation, audit and negative HTTP permission tests. The fixture
uses random schemas and never modifies `public`. Without opt-in those tests skip.
No type checker is configured; Ruff and compileall provide repository static checks.
