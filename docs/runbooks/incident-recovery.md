# Incident and troubleshooting recovery

Record time/release/safe job-request IDs/dependency health. Use bounded logs/audit;
exclude credentials, config files, raw driver errors and client profiles. Preserve
DB volumes/ciphertext; do not flush/reset as initial triage.

| Symptom | Check and recovery |
| --- | --- |
| Backup failed | Safe job code/`BACKUP_FAILED`, config permissions/key ID, reader grants/tool major, S3 TLS/policy, tmpfs/size/time limits. Correct config, restart worker, run a new job. |
| Backup unknown | Upload may have persisted. CLI verify UUID with original namespace/keyring. Never overwrite or manually mark history successful; record evidence, run fresh job. |
| Queued jobs | Worker/readiness and PostgreSQL/Redis network/auth. Durable dispatch repairs hints; never manufacture Redis jobs. |
| Lost worker/deadline | Allow lease recovery; inspect retry budget and failed/unknown outcome. Fencing stops late DB writes; reconcile external effects separately. |
| Redis unavailable | Restore private/authenticated connectivity; PostgreSQL retains jobs/audit/Notifications/SSE history. |
| PostgreSQL unavailable | Stop mutations; check service/storage/space/network. Preserve volume; restore new DB for corruption/host loss. |
| Missing key | Recover escrowed key; new key cannot decrypt retained objects. Application master key separately protects SSH/VPN/TOTP. |
| Restore refused | UUID/confirmation/empty target/archive/key retention. Recreate only new/disposable target; never bypass validation or use `--clean`. |
| Node alert | Check fresh collector evidence, [alerts](../operational-alerts.md)/[monitoring](../admin-visibility.md). CP loss need not remove working VPN nodes. |

After recovery verify auth/RBAC/private exposure/durable jobs/node state; run fresh
backup. Record recovery point and reconcile changes since snapshot. Follow
[deployment](control-plane-deployment.md), [upgrade](upgrade-migrations.md) and
[backup/restore](backup-restore.md) boundaries.
