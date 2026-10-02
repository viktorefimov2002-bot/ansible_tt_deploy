# Control Plane deployment

Use [Compose runtime](../../infra/compose/README.md) and
[admin bootstrap](../authentication.md). Record release/images and migration revision.

1. Prepare management host, Docker/Compose 2.24.4+, memory/storage, distinct admin/client
   DNS, trusted TLS, outbound SSH/S3. Restrict ingress; keep PostgreSQL/Redis private.
2. Protect environment/master key/TLS and [backup config](../backups.md) outside checkout
   and images. Configure key escrow, reader/storage permissions and retention.
3. Build exact checkout, validate staging, start PostgreSQL/Redis, run `migrate` as
   owner. Create separate runtime/backup logins with protected DBA tooling; apply
   runtime-grants.sql/backup-grants.sql. Runtime must not own schema/mutate audit.
   Never print real `docker compose config` output: it expands secrets.
4. Start from repository root:

```sh
docker compose --env-file infra/compose/.env \
  -f infra/compose/compose.yaml -f infra/compose/compose.production.yaml \
  -f infra/compose/compose.backup.yaml up -d --build
```

5. Check container health, `/api/readyz`, TLS/origin isolation, admin/TOTP login, viewer
   denial, worker/Redis recovery, collector/metrics and safe logs. Enroll initial
   admin using protected offline bootstrap.
6. Run remote backup and [disposable restore drill](backup-restore.md). Assign age/
   monitoring responsibility and escrow keys/config independently.

For host loss rebuild recorded release and follow recovery cutover. Data Plane remains
independent; do not remove working VPN nodes during management-plane recovery.
