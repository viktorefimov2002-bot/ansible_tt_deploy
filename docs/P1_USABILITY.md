# P1 usability workflow

This document describes the safer P1 workflow added on top of the existing deployment scripts.

The existing `bootstrap.sh`, `site.yml`, `uninstall.yml`, Ansible role and generated `deploy.sh`/`uninstall.sh` flow are still supported. The new `ttctl` helper is an optional wrapper intended to reduce repeated manual actions.

## One-file deployment

Copy the example config:

```bash
cp deploy.example.yml deploy.yml
```

Edit `deploy.yml`, then generate runtime files:

```bash
./ttctl init --config deploy.yml
```

This creates:

- `inventory.ini`
- `vars.yml`
- optionally a copied `roles/trusttunnel_endpoint/files/credentials.toml` when `existing_credentials_source` is set

Then deploy:

```bash
./ttctl deploy
```

## Non-root SSH user

`deploy.yml` supports a non-root SSH user:

```yaml
server:
  host: 203.0.113.10
  ssh_user: ubuntu
  become: true
```

This generates an inventory entry with `ansible_user=ubuntu ansible_become=true`.

## Import existing clients

To migrate users from an existing `credentials.toml`, point the config at the local source file:

```yaml
trusttunnel:
  existing_credentials_source: ./credentials.toml
  existing_credentials_file: credentials.toml
  clients: []
```

`./ttctl init --config deploy.yml` copies the file into `roles/trusttunnel_endpoint/files/credentials.toml` with mode `600` and sets `trusttunnel_existing_credentials_file` in `vars.yml`.

You can also import manually:

```bash
./ttctl import-credentials ./credentials.toml
```

## Daily operations

Check service status:

```bash
./ttctl status
```

Add clients through the existing helper:

```bash
./ttctl add-client
```

Remove clients through the existing helper:

```bash
./ttctl remove-client
```

Uninstall:

```bash
./ttctl uninstall
```

## Secrets

`ttctl init` writes `vars.yml` with mode `600`. For real SSH, sudo or VPN passwords, prefer the existing `bootstrap.sh` + Ansible Vault flow until Vault support is added directly to `ttctl init`.

## Rollback

See [ROLLBACK.md](ROLLBACK.md).

The important safety property is that this P1 change is additive: if `ttctl` does not work for your environment, continue using the existing `bootstrap.sh` flow.
