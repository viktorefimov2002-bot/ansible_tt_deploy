# Upgrade and migrations

1. Record current/target release, `alembic current`, desired head and compatibility.
   Read migrations/release notes; rehearse restored staging with node egress disabled.
2. Run backup, wait for verified success and independent CLI verification. Confirm
   off-host key escrow, maintenance/recovery limits; record UUID.
3. Prevent new mutations; stop API/worker/collector with complete deployment Compose
   file set. Let active jobs finish when safe; review interrupted work. Confirm no
   dump before DDL. Redis is not durable migration state.
4. Build desired images. Run `docker compose ... --profile tools run --rm migrate` as
   schema owner with base/production/backup file set. Inspect status, run `alembic current`
   in same image; reapply runtime/reader grants for new tables/object creators.
5. Start API, check readiness/auth/RBAC/Notifications/reads; start worker/collector,
   inspect recovered jobs/configuration, then traffic. Run and verify a fresh backup.

Prefer forward fixes. Do not automatically downgrade or run incompatible older code.
For data recovery, stop apps and restore recorded UUID into a new empty database via
[restore runbook](backup-restore.md). Reconcile jobs/external effects before cutover;
record accepted lost changes. Preserve previous volume until recovery/release acceptance.
