# Backup and restore verification

Prerequisites: [protected config](../backups.md), PostgreSQL 17 tools (worker image),
migrated Control Plane, private off-host S3 prefix and independent recovery-key escrow.

## Run and independently verify

Admin Web **Backups → Run backup now** queues a durable operation. Wait for verified
success; record UUID. Viewers read history/age/logs only. API clients use the fixed
authenticated POST with one UUID key; reuse after uncertain responses. Keep session
credentials out of argv; do not automate password/TOTP. From repository root:

```sh
docker compose --env-file infra/compose/.env \
  -f infra/compose/compose.yaml -f infra/compose/compose.backup.yaml \
  --profile tools run --rm backup-maintenance verify <job-uuid>
```

Installed-tool equivalent: `python -m apps.backups verify <job-uuid>`, with protected
`TTCP_BACKUP_CONFIG_FILE`. Only ciphertext is spooled. Verification proves authenticated
identity/compression integrity; successful restore/data comparison provides stronger proof.

## Disposable backup → restore drill

1. Use a separate disposable Compose project/PostgreSQL instance/volumes, disposable
   credentials/keyring and S3 bucket/prefix on another host/provider. Never production.
   Migrate and seed recognizable user/device data, `device_limit=7` and an audit record
   through supported flows. Record expected counts and migration revision.
2. Run backup and independent verify above; record UUID.
3. Create a **new empty** database `ttcp_restore_drill` owned by the maintenance role.
   Do not migrate first: the dump includes schema/revision. Prepare another protected
   JSON containing only Postgres fields from backup config, but target database and
   maintenance credentials. Export absolute path as `TTCP_RESTORE_CONFIG_PATH`; owner
   UID 65534/mode 0600. Mount only in maintenance, never API/worker.
4. Stop applications on a recovery target and isolate it from other writers. Run:

```sh
docker compose --env-file infra/compose/.env \
  -f infra/compose/compose.yaml -f infra/compose/compose.backup.yaml \
  -f infra/compose/compose.restore.yaml --profile tools run --rm \
  backup-maintenance restore <job-uuid> --confirm-database ttcp_restore_drill
```

Local: `python -m apps.backups restore <job-uuid> --confirm-database ttcp_restore_drill`,
with protected `TTCP_BACKUP_CONFIG_FILE` and `TTCP_RESTORE_CONFIG_FILE`. Source database
and occupied targets are refused. Whole first pass authenticates before target access;
second pass streams to `pg_restore --exit-on-error --single-transaction`. SQL errors
roll back. No plaintext files, `--clean`, arbitrary paths or Web restore.

5. Compare revision, seeded user/quota/device, immutable audit and counts/constraints.
   Restore separately escrowed application master key into isolated API and test
   login/decryption. Keep managed-server egress blocked.
6. Wrong confirmation, occupied target and tampered/truncated **disposable ciphertext
   copy** must refuse with target counts unchanged. Record UUID/time/age/counts/results;
   never record credentials or keys.
7. Remove only drill databases/volumes/objects with DBA/storage operator tooling.
   Application credentials cannot delete remote objects.

Automated drill, with protected `TTCP_POSTGRES_*` targeting a disposable DBA database
and PostgreSQL 17 `pg_dump`/`pg_restore` on PATH:

```sh
TTCP_TEST_POSTGRES=1 TTCP_TEST_BACKUP_ROUNDTRIP=1 \
  python -m pytest -q tests/test_backups.py tests/test_backup_jobs.py tests/test_backup_restore.py
```

It uses real PostgreSQL/schema/data and maintenance workflow with a test S3 HTTP peer.
Repeat the operational drill against your selected off-host provider.

## Disaster recovery cutover

1. Preserve evidence; select a verified UUID within the accepted recovery point.
   Recover keyring/application key and original namespace. Restore into a new empty
   database above; source PostgreSQL need not be available to fetch archives.
2. Recreate global roles and reapply runtime/reader grants (dump omits owners/ACLs).
   Keep API/worker/collector stopped and target egress isolated.
3. Dump predates its own completion receipt/audit and may contain pending/running jobs.
   Quarantine old intents in a DBA session on the **restored target**:

```sql
BEGIN;
UPDATE jobs SET status='failed', finished_at=clock_timestamp(),
  claim_token=NULL, lease_until=NULL, error_code='unsafe_outcome',
  error_message='Execution outcome requires reconciliation'
WHERE status IN ('queued','running');
INSERT INTO audit_events(actor_type,action,target_type,result)
VALUES ('system','backup.restore','control_plane','success');
COMMIT;
```

4. Reconcile server lifecycle/configuration, pending credentials/revocations, invitations
   and alerts against actual Data Plane effects. Revoke restored admin/client sessions
   and unused invitations through an audited DBA procedure so old snapshots do not
   revive access. Preserve immutable audit.
5. Point protected settings at recovered DB; apply required forward migrations. Start
   API and check auth/RBAC, then worker/collector after reconciliation. Run a fresh
   verified backup; retain previous database until acceptance. Record lost changes.

Agree schedule/RPO, monitor age and run regular drills. Automatic retention, WAL/PITR
and scheduling service are out of scope; green upload alone is not restore readiness.
