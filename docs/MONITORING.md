# Monitoring and diagnostics

This project currently provides a first monitoring layer through `ttctl status`.

It is a manual diagnostics command, not a background alerting system.

## Status modes

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

## What is checked

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

## Output behavior

`ttctl status` suppresses normal Ansible task output and prints only the final report.

If diagnostics fail, it prints the full Ansible output to help with troubleshooting.

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

## Later alerting options

Future monitoring can add:

- server-side healthcheck script;
- systemd timer;
- webhook notifications;
- external checks with Uptime Kuma, Prometheus Blackbox Exporter, Zabbix, or another monitoring system.
