# ADR-0023: Encrypted logical backups through fixed remote objects

- Status: Proposed; implemented for the explicit TTCP-020 request
- Date: 2026-10-02
- Sources: architecture sections 19, 22–23, 25–26, 30; TTCP-020; ADR-0013/0019

## Decision

Reuse PostgreSQL jobs for intent, history and receipts; Redis carries UUID hints only.
Serialize requests with a transaction advisory lock and allow one active backup.
Existing worker fencing, three attempts, backoff and lease recovery apply.

Pipe a fixed PostgreSQL 17 custom dump through gzip and streaming AES-256-GCM with a
dedicated key. `TTCPBK01` authenticates version, job UUID and key ID as associated data.
A random 96-bit nonce and trailing 128-bit tag authenticate the complete archive.
Only ciphertext reaches files. Runtime secrets are mounted in worker/maintenance only.

`BackupStorage` exposes download and conditional upload by UUID. S3 uses TLS, SigV4
signed payloads and `<fixed-prefix>/<job-id>.ttcpbk` with `If-None-Match: *`. There is
no overwrite/list/delete capability. Retries authenticate existing objects before
dumping. Success requires remote readback, a fenced receipt and atomic audit.
Unconfirmed interrupted uploads become `unknown` after retry exhaustion.
Audit-backed Notifications include `BACKUP_FAILED` for failed/unknown outcomes.

Restore has no API/UI/job handler. Maintenance authenticates a complete first pass
before connecting to an explicitly confirmed empty target, then streams a second
pass into `pg_restore --single-transaction`. Never overwrite source, use `--clean`,
or materialize plaintext. Isolate the target during maintenance.

## Alternatives and consequences

- Plaintext spool violates scope; whole-buffer encryption scales memory with size.
  Streaming GCM uses existing primitives; two-pass restore excludes unauthenticated data.
- Narrow SigV4 HTTP adapter reuses httpx without a new application dependency.
  Providers must support path-style requests/conditional PutObject; verify each provider.
- Defaults: 48 MiB ciphertext, 1 GiB plaintext, 600-second attempt. Larger archives
  require protected limit changes and tmpfs/memory sizing. Readback costs bandwidth.
- A dump precedes its own completion audit. Quarantine restored jobs and reconcile
  external effects before worker startup. Global roles/runtime config/master key
  need independent off-host escrow.
- Preserve old keys/namespaces. Change providers by copying ciphertext out of band
  and verifying UUIDs. Retention/scheduling/drills remain operator duties.
- No host-specific cron loop, deletion capability, WAL/PITR or scheduler service ships.

## Validation

Tests cover integrity/truncation/identity/keys, bounds/secrets, conditional writes,
ambiguous replies, retries/fencing/RBAC/audit/Notifications/UI and actual disposable
PostgreSQL dump/restore. Real provider and container-build validation are deployment
checks. Owner acceptance of this Proposed ADR remains pending.

Protocol references: [S3 conditional PutObject](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutObject.html),
[SigV4 signing](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_sigv-create-signed-request.html),
[GCM authentication](https://cryptography.io/en/46.0.3/hazmat/primitives/symmetric-encryption/),
[PostgreSQL restore](https://www.postgresql.org/docs/17/app-pgrestore.html).
