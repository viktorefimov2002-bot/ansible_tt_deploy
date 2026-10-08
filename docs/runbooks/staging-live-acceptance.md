# Explicit staging/live release acceptance

This is an operator checklist, never a CI job. Execute only after explicit approval
for the named staging resources and actions. A live production drill additionally
needs a scheduled maintenance window and a recorded rollback/restore decision.
Do not substitute production credentials into a CI harness.

Record: release revision/image digests, date/operator, approved node addresses and
SSH fingerprints, separate Admin/Client HTTPS origins, S3 provider/bucket/prefix,
current Alembic head, expected RPO/RTO and the prior recoverable release. Keep secret
values/configurations outside the acceptance record. For each row record PASS/FAIL,
safe job/audit IDs, observation time and measured recovery; blanks mean pending.

## Before changing resources

- [ ] All mandatory [release CI gates](../release-readiness.md) passed for this revision.
- [ ] Use isolated staging users/nodes/storage; firewall SSH and metrics to approved
  control-plane addresses. Inventory contains only the approved staging nodes.
- [ ] Follow [deployment](control-plane-deployment.md), [upgrade](upgrade-migrations.md)
  and [backup/restore](backup-restore.md) runbooks. Escrow runtime/master/backup keys
  and role/grant configuration off-host; verify access without placing them in logs.
- [ ] Capture a verified encrypted off-host backup and prove the previous release's
  rollback/forward-fix path. Obtain a staging node snapshot for destructive drills.
- [ ] Confirm distinct trusted HTTPS hosts, host-only cookies, unknown SNI/API denial,
  no CORS permission, viewer denials and no publicly reachable SQL/Redis/metrics port.

## Managed-node lifecycle and VPN

- [ ] Bootstrap one node using provider SSH password and another using a key. Verify
  both fingerprints out of band. Reject a wrong fingerprint and failed authentication;
  verify raw provider credentials are absent from SQL/jobs/logs and temporary files.
- [ ] Run preflight; inspect DNS, reachability, ports, memory/disk and pinned management
  identity. Deploy an explicit supported TrustTunnel release; verify service health,
  least-privilege helper ownership and audit/job completion. Repeat safely and inspect
  idempotency. A job success without actual service health is a failure.
- [ ] Update to the selected version, verify the running binary/version and existing
  clients. Rehearse failure partway through an update on the disposable node and
  reconcile any uncertain outcome before retrying. Verify serialized node operations.
- [ ] Apply a valid configuration revision and observe it on the node. Inject a
  controlled invalid/unhealthy revision on staging; verify automatic rollback to the
  exact previous revision, durable failure/rollback state and notification. Restart
  the worker at an apply boundary and reconcile actual remote state before retry.
- [ ] Complete the same invitation/device/config journey through both trusted origins
  with a real TrustTunnel client. Check a non-default device limit, two server
  credentials on one device and disabled/expired access denial.
- [ ] Establish VPN traffic to each staging node; confirm expected exit address,
  DNS, IPv4/IPv6 policy and representative application traffic. Temporarily stop the
  control plane: existing authorized VPN traffic must continue.
- [ ] Revoke the device/credential, verify it disappears on the node, new configuration
  delivery is denied and actual reconnect fails. Test documented semantics for already
  established sessions; record any revocation latency rather than assuming immediacy.
- [ ] Rehearse explicit restart and uninstall only on the disposable node. Verify
  recoverability, truthful job state, notifications and audit results.

## Monitoring and failure recovery

- [ ] Verify pinned exporter/service observations reach VictoriaMetrics and Grafana;
  absent/stale/unknown observations must not appear healthy. Confirm API, worker,
  PostgreSQL and Redis health, collector freshness and reasonable label cardinality.
- [ ] Create controlled node-offline/service-down and sustained disk/memory pressure
  on disposable nodes. Observe alert grace periods, one durable incident/notification,
  no duplicates across worker/Redis restarts and a correct recovery transition.
  Record unsupported alert classes separately (see [alerts](../operational-alerts.md)).
- [ ] Interrupt PostgreSQL/Redis/worker separately within the approved window. Verify
  readiness degradation, durable intent/history, bounded retries/fencing and eventual
  recovery. For unsafe remote attempts verify uncertain outcomes require inspection.
- [ ] Confirm least-privilege runtime and backup reader roles after migration; test
  reader write denial and application audit-update/delete denial using staging roles.

## Real off-host backup and restore

- [ ] Use the provider's dedicated restricted S3 identity over verified TLS. Verify
  fixed-prefix read/conditional-write permissions, overwrite denial, denied unrelated
  buckets/prefixes and documented retention/scheduling without granting runtime deletion.
- [ ] Run a backup, authenticate remote readback and record UUID/key ID/size/completion
  time and audit/notification. Demonstrate a provider outage/ambiguous upload and safe
  retry of the same UUID without silently overwriting an existing object.
- [ ] Download only ciphertext; reject corruption, truncation, wrong key and wrong
  UUID before a target connection. Do not log or export decrypted client material.
- [ ] Restore through maintenance into an explicitly confirmed **empty isolated**
  target, with node egress and worker disabled. Refuse source/occupied targets. Verify
  Alembic head/model consistency, users/devices/access/credentials, config revisions,
  immutable audits and keys needed for decryption. Reapply runtime/reader grants.
- [ ] Follow restored-access/job quarantine from the restore runbook: invalidate
  sessions/invitations, stop/reconcile historical pending jobs and confirm old browser
  tokens cannot authenticate. Reconcile credentials and actual node state before
  enabling workers or exposing the restored service.
- [ ] Perform a cold control-host rebuild from independent key/runtime escrow plus
  remote backup; measure RPO/RTO, origin/TLS restoration and ongoing VPN traffic.
  Preserve the old volume until acceptance and any loss of newer changes is approved.

## Release decision

- [ ] Record every failed/pending item, owner, impact and required remediation.
- [ ] Confirm no generated keys/passwords/invitations/configs entered the repository,
  logs or CI artifacts. Store safe acceptance evidence in the release record.
- [ ] Owner explicitly signs off the revision and any accepted limitations, including
  relevant Proposed ADRs. No checklist completion automatically deploys or publishes.

