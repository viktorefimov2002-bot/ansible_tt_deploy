# Monitoring and diagnostics

The project has two monitoring/diagnostics layers:

1. manual diagnostics through `ttctl status`;
2. optional server-side healthcheck through a systemd timer.

The server-side healthcheck is local monitoring on the VPN server. It writes results to journald and returns a meaningful exit code. It does not send external notifications yet.

## Manual status modes

Human-readable report:

```bash
./ttctl status
```

Short one-line report:

```bash
./ttctl status --short
```

JSON output:

```bash
./ttctl status --json
```

Client/config drift report:

```bash
./ttctl status --clients
```

Recent service logs:

```bash
./ttctl status --logs
```

Control the number of checked log lines:

```bash
./ttctl status --logs --log-lines 100
./ttctl status --short --log-lines 100
```

`ttctl status` suppresses normal Ansible task output and prints only the final report. If diagnostics fail, it prints the full Ansible output for troubleshooting.

## What `ttctl status` checks

Remote server checks:

- `trusttunnel.service` active/enabled state;
- TCP listener on the TrustTunnel port;
- UDP listener on the TrustTunnel port;
- install directory;
- `/opt/trusttunnel/credentials.toml`;
- number of users in `credentials.toml`;
- server-side `client_<username>.toml` files;
- Let's Encrypt certificate file and expiration warning;
- recent warning/error log lines from `journalctl`.

Local controller checks:

- fetched files under `client_configs/`;
- configs missing locally for users from `credentials.toml`;
- configs missing on the server for users from `credentials.toml`.

## Interpreting Overall

`Overall: OK` means the core server checks passed:

- service is active;
- TCP port is listening;
- UDP port is listening;
- `credentials.toml` exists;
- at least one user was parsed from `credentials.toml`.

Missing local configs do not make `Overall` fail. They mean the local controller does not have all client config files yet.

To sync server credentials and fetch missing local configs:

```bash
./ttctl add-client --sync-from-server
```

## Server-side healthcheck timer

Enable it in `deploy.yml`:

```yaml
trusttunnel:
  monitoring_enabled: true
  monitoring_interval: 2min
  monitoring_randomized_delay: 30s
  monitoring_log_lines: 50
  monitoring_cert_warning_days: 14
  monitoring_require_tcp: true
  monitoring_require_udp: true
  monitoring_require_client_configs: false
```

Then regenerate `vars.yml` and deploy:

```bash
./ttctl init --config deploy.yml
./ttctl deploy
```

If you use Vault-backed SSH/sudo secrets, keep using the same init flags as before, for example:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
./ttctl deploy
```

When enabled, deploy installs:

- `/usr/local/bin/trusttunnel-healthcheck`;
- `trusttunnel-healthcheck.service`;
- `trusttunnel-healthcheck.timer`.

The timer runs the healthcheck periodically. The healthcheck exits with:

- `0` when checks pass;
- `1` when one or more checks fail.

## Inspecting the healthcheck

Check timer status:

```bash
systemctl status trusttunnel-healthcheck.timer
```

Run the healthcheck manually on the server:

```bash
/usr/local/bin/trusttunnel-healthcheck
```

View recent healthcheck runs:

```bash
journalctl -u trusttunnel-healthcheck.service -n 50 --no-pager
```

List timers:

```bash
systemctl list-timers --all | grep trusttunnel-healthcheck
```

## What the healthcheck checks

The server-side healthcheck checks:

- `trusttunnel.service` is active;
- install directory exists;
- `credentials.toml` exists;
- at least one user exists in `credentials.toml`;
- TCP listener exists if `monitoring_require_tcp=true`;
- UDP listener exists if `monitoring_require_udp=true`;
- Let's Encrypt certificate exists and is not close to expiration;
- recent logs do not contain error-like lines;
- server-side client configs exist when `monitoring_require_client_configs=true`.

`monitoring_require_client_configs` defaults to `false` because missing generated config files do not necessarily mean the running VPN service is broken.

## Uninstall behavior

`./ttctl uninstall` removes monitoring units and the healthcheck script when:

```yaml
trusttunnel_remove_monitoring: true
```

This is the default.

## Later alerting options

Future monitoring can add:

- Telegram notifications;
- webhook notifications;
- external checks with Uptime Kuma, Prometheus Blackbox Exporter, Zabbix, Better Stack, or another monitoring system.
