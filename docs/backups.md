# Remote backups — TTCP-020

Implementation: `apps/backups/`, `apps/api/backups.py`, Admin Web **Backups**.
See [ADR-0023](adr/0023-remote-logical-backups.md) and
[backup/restore](runbooks/backup-restore.md). Scope is the implementation request;
there is no separate TTCP-020 task file in the repository.

## API and lifecycle

- `GET /api/backups?offset=0&limit=50`: authenticated admin/viewer; paginated history,
  latest operation, last verified backup and conservative snapshot age in seconds.
- `POST /api/backups/run`: admin only, `{"idempotency_key":"<UUID>"}`, HTTP 202.
  Reuse the key after uncertain responses. Extra fields: 422; another active backup
  or another operator's key: 409; disabled backups: 503.
- No restore, storage settings, arbitrary bucket/path/command or download API.
  Client-origin NGINX excludes backup routes. Responses disable caching.

Existing PostgreSQL `backup.run` jobs retain three attempts, bounded exponential
backoff, 600-second default timeout and a lease 30 seconds longer. Redis carries UUIDs
only; durable dispatch repairs lost hints. Queued cancellation is allowed through
the existing job endpoint; running backup cancellation is denied.

History states: queued/running/succeeded/failed/unknown/cancelled. `unknown` is a backup
outcome over a failed Job. Retries authenticate fixed remote objects before dumping
and never overwrite them. Success requires readback. Terminal audit atomically projects
one `BACKUP_SUCCEEDED` or `BACKUP_FAILED` Notification (including unknown outcome).
Secrets/provider errors never enter metadata/logs.

## Protected configuration

Use [compose.backup.yaml](../infra/compose/compose.backup.yaml). Keep JSON outside the
checkout and export only its absolute path as `TTCP_BACKUP_CONFIG_PATH`. Host owner:
UID 65534; mode: 0600/0400. Mount is read-only. Loader rejects nonregular/oversized
files and POSIX group/world access. Windows development requires a restrictive ACL.
API receives only an enabled flag; only worker/maintenance receives the file.

Replace these placeholders in a protected editor, never in shell/log output:

```json
{
  "endpoint": "https://s3.example.net", "region": "us-east-1",
  "bucket": "ttcp-backups", "prefix": "control-plane",
  "access_key": "<restricted storage key>", "secret_key": "<storage secret>",
  "active_key_id": "backup-2026-01",
  "encryption_keys": {"backup-2026-01": "<64 random hexadecimal characters>"},
  "postgres": {
    "host": "postgres", "port": 5432, "database": "ttcp",
    "user": "ttcp_backup", "password": "<reader password>", "sslmode": "disable"
  },
  "max_encrypted_bytes": 50331648, "max_plain_bytes": 1073741824,
  "timeout_seconds": 600
}
```

Generate the dedicated key with `secrets.token_hex(32)` inside the protected config
writer. Never print it or reuse the application key. Optional `session_token` supports
temporary S3 credentials. S3 requires TLS certificate validation; redirects/proxies
are disabled. Endpoint has no credentials/path/query.

`sslmode=disable` is for isolated Compose networking. Remote PostgreSQL should use
`verify-full` with a trusted root certificate installed for the maintenance UID.
`require` encrypts without checking identity. Passwords go in child environment, not argv/files.

Create a separate non-superuser reader through protected DBA tooling and apply
[backup-grants.sql](../infra/compose/postgres/backup-grants.sql) as schema owner.
Restrict S3 permissions to GetObject and conditional PutObject on the fixed prefix;
deny public access/deletion, require TLS, configure provider retention/versioning/Object
Lock. Workers cannot delete backups.

Escrow backup keyring, **application master key**, storage config, global role setup
and TLS/edge configuration independently off-host. Dumps omit these, PostgreSQL
global roles, Data Plane files and monitoring volumes. Retain old keys when rotating
`active_key_id`; restart worker. Preserve old namespace/provider configuration or copy
ciphertext and verify before switching.
