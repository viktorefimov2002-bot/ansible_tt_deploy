# Managed-node onboarding — TTCP-010

This is separate from `automation/ansible/bootstrap.sh` (controller/CLI setup).
Scope comes from the TTCP-010 implementation request, architecture section 7 and
[ADR-0015](adr/0015-managed-node-bootstrap.md). No VPN installation is performed.

## Prerequisites and initial access

Supported nodes: Ubuntu 22.04/24.04 or Debian 12/13, x86_64/aarch64, with systemd,
OpenSSH server and working distribution apt repositories. Run on a fresh VPS as
root. The shell entry point checks OS/CPU/root before any package installation and
installs Python if absent. Subsequent unsafe-state checks can fail after that
interpreter installation. Do not invoke the Python implementation on other hosts.

Obtain the **full SSH host public key** from the authenticated provider console.
Construct a protected local known_hosts file from that verified key, using
`[hostname]:port` for a nonstandard port. Do not use ssh-keyscan as a trust source.
Use `StrictHostKeyChecking=yes`, the verified UserKnownHostsFile and
`GlobalKnownHostsFile=/dev/null` for both initial access and verification. A
mismatch stops onboarding; verify out of band, never bypass the check.

For initial password access, use the interactive OpenSSH password prompt, with
public-key authentication disabled if necessary. For initial key access, use the
provider identity file with IdentitiesOnly=yes. Never pass a password on argv,
write it to inventory or send it to the Control Plane. Keep the recovery session
open while testing the dedicated identity. No initial credential is read by the
bootstrap script or stored in the API, database, Redis, repository or artifacts.

## Run on the node

Copy `bootstrap-node.sh`, `bootstrap_node.py`, and `node_credential.py` from `scripts/` to a root-controlled directory on the
node through that verified connection or provider console. Transfer only the
management **public** key to `/root/ttcp-management.pub` outside the repository;
strip its optional comment so it contains exactly `ssh-ed25519 BASE64`.
Keep the private key on the operator/Control Plane side. From the root session:

```sh
sh /root/ttcp-bootstrap/bootstrap-node.sh < /root/ttcp-management.pub
```

Do not use shell tracing or a terminal session recorder when handling credentials.
There are no command-line key/password options. Output contains fixed diagnostics
only. The public key persists only where required: root-managed node SSH access.
Bootstrap takes a nonblocking local lock. Concurrent invocation fails; rerun after
the first attempt finishes. A failed command returns nonzero and does not report
the node prepared. Inspect local services/package state, repair and rerun with the
same public key. Do not delete the ownership marker to force adoption.

The script creates `ttcp`, locks its password, sets its home to mode 0700, and
restricts SSH to public-key authentication with forwarding, PTY and user RC disabled.
The root-owned key file is world-readable because sshd reads it as `ttcp`; it
contains only the public key. The account can write its home for Ansible temporary
files but cannot change its authorized key or privilege helper. Sudo accepts only
`/usr/local/sbin/ttcp-node-status` and `/usr/local/sbin/ttcp-node-credential`
**without arguments**. The latter accepts only credential create/revoke JSON on
stdin and updates `/opt/trusttunnel/credentials.toml` atomically. It does not grant shell,
interpreter, package manager, systemctl restart or arbitrary Ansible become access.
The helper reports fixed systemd service and process properties, the automatic
restart count, and a running version when the endpoint's documented
`--version` response is safely available. Rerun bootstrap with the same key
on an already prepared node to install the updated helpers for TTCP-011/012.

An existing `ttcp` account/configuration without the exact bootstrap ownership
marker is rejected. Repeated runs keep the same key; differing keys, privileged
groups, unsafe homes, symlinks or non-root-controlled configuration fail closed.
Foreign exporter configuration also requires operator review. The workflow owns
its named files; it repairs those files on rerun. It does not remove other sudo or
SSH policy installed by an administrator. Custom SSH Allow/Deny/Match restrictions
must permit the management account from the actual Control Plane address.

The exporter is installed from the distribution repository with a systemd override
listening on `127.0.0.1:9100`. No network port is opened. TTCP-011 uses a pinned
SSH command as `ttcp` to read it over node loopback; forwarding stays disabled.
See [ADR-0016](adr/0016-observability-transport.md).
No full system upgrade, Ansible controller, Docker, VPN binary or monitoring backend
is installed on the node.

## Verify and register

From the future Control Plane network, using the verified host pin and management
private key, establish a **new** SSH connection as `ttcp` (BatchMode=yes):

```sh
ssh -F /dev/null -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/secure/known_hosts \
  -o GlobalKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o BatchMode=yes \
  -i /secure/management-key ttcp@node.example \
  'sudo -n /usr/local/sbin/ttcp-node-status'
```

Adjust hostname/port and paths, never disable strict checking. A fresh node may
report `LoadState=not-found`; that is expected before VPN deployment. Verify
negative permissions (`sudo -n /bin/true` and helper arguments must fail). From the
provider console check `ss -lntp` shows exporter only on 127.0.0.1:9100 and
`systemctl is-active prometheus-node-exporter`. Run bootstrap a second time and
repeat these checks on a disposable node before production rollout.

Only after successful new-connection verification register through the existing
[server API](servers.md) with `ssh_user=ttcp`, the verified host key and the matching
management private key over the authenticated API. The API encrypts the management
key and audits registration/rotation under ADR-0014. Run preflight and status jobs;
review preflight checks, including DNS, port availability and time synchronization.
Bootstrap does not assert that every deployment prerequisite passes or update DB
readiness. Retire provider credentials according to operator policy after verifying
recovery access. Host and management key rotation remain explicit operations.

## Validation boundaries

`tests/test_node_bootstrap.py` exercises real file configuration/admission logic
with fake OS commands, including retries, unsafe states, key protection, sudo rules
and SSH validation failure. POSIX atomic-file permissions have a separate test.
These tests do not substitute for live apt, sudo, sshd and systemd acceptance on
each supported distribution. Windows cannot execute those Linux service checks.
