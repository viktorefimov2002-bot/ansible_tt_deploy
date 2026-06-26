# P1 usability workflow

This document describes the safer P1 workflow added on top of the existing deployment scripts.

The existing `bootstrap.sh`, `site.yml`, `uninstall.yml`, Ansible role and generated `deploy.sh`/`uninstall.sh` flow are still supported. The new `ttctl` helper is an optional wrapper intended to reduce repeated manual actions.

## Prepare helper

After pulling this branch, make the helper executable if your checkout did not preserve the executable bit:

```bash
chmod +x ttctl
```

You can also run it explicitly through Bash:

```bash
bash ./ttctl --help
```

## One-file deployment

Copy the example config:

```bash
cp deploy.example.yml deploy.yml
```

Edit `deploy.yml`, then generate runtime files.

For SSH key access:

```bash
./ttctl init --config deploy.yml
```

For SSH password access, use Vault-backed prompting instead of putting the password into `deploy.yml`:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

For sudo/become password access:

```bash
./ttctl init --config deploy.yml --ask-become-pass
```

For both SSH password and sudo password:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

The password prompts use hidden terminal input. `ttctl` writes `ansible_password` and/or `ansible_become_password` to `vault.yml` and immediately encrypts it with Ansible Vault.

This creates:

- `inventory.ini`
- `vars.yml`
- optional encrypted `vault.yml`
- optionally a copied `roles/trusttunnel_endpoint/files/credentials.toml` when `existing_credentials_source` is set

Then deploy:

```bash
./ttctl deploy
```

When `vault.yml` exists, deploy/status/uninstall/add-client/remove-client automatically include it. During one `ttctl` command run, the Vault password is requested once, stored in a temporary mode-600 password file, reused by internal Ansible calls, and removed on exit.

## Non-root SSH user

`deploy.yml` supports a non-root SSH user:

```yaml
server:
  host: 203.0.113.10
  ssh_user: ubuntu
  become: true
```

This generates an inventory entry with `ansible_user=ubuntu ansible_become=true`.

If SSH uses a password:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass
```

If sudo also asks for a password:

```bash
./ttctl init --config deploy.yml --ask-ssh-pass --ask-become-pass
```

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

## Add clients after deployment

Prepare the default input file:

```text
roles/trusttunnel_endpoint/files/new_clients.toml
```

Then run:

```bash
./ttctl add-client
```

This is a lightweight post-install operation, not a full deploy.

It does the following:

1. fetches `/opt/trusttunnel/credentials.toml` from the server if the local file is missing;
2. appends only new usernames to the local credentials file;
3. skips duplicate usernames;
4. checks that `trusttunnel.service` is active;
5. uploads the updated credentials file back to `/opt/trusttunnel/credentials.toml`;
6. runs `systemctl reload-or-restart trusttunnel`;
7. verifies that the service is still active;
8. generates and fetches client configs to `client_configs/`.

Force a fresh server sync before adding:

```bash
./ttctl add-client --sync-from-server
```

Add clients locally but do not apply immediately:

```bash
./ttctl add-client --no-apply
```

`--no-deploy` remains accepted as an alias for `--no-apply`.

Use a custom input file:

```bash
./ttctl add-client ./my_new_clients.toml
```

## Remove clients after deployment

Prepare the default input file:

```text
roles/trusttunnel_endpoint/files/remove_clients.txt
```

Then run:

```bash
./ttctl remove-client
```

This is also a lightweight post-install operation, not a full deploy.

It does the following:

1. fetches `/opt/trusttunnel/credentials.toml` from the server if the local file is missing;
2. removes requested usernames from the local credentials file;
3. checks that `trusttunnel.service` is active;
4. uploads the updated credentials file back to `/opt/trusttunnel/credentials.toml`;
5. runs `systemctl reload-or-restart trusttunnel`;
6. verifies that the service is still active;
7. removes generated client config files for those users on the server and locally.

Force a fresh server sync before removing:

```bash
./ttctl remove-client --sync-from-server
```

Remove clients locally but do not apply immediately:

```bash
./ttctl remove-client --no-apply
```

## Daily operations

Check service status:

```bash
./ttctl status
```

Add clients:

```bash
./ttctl add-client
```

Remove clients:

```bash
./ttctl remove-client
```

Uninstall:

```bash
./ttctl uninstall
```

## Secrets

Preferred modes:

- SSH key: no SSH password in any project file.
- SSH password: `./ttctl init --config deploy.yml --ask-ssh-pass`.
- sudo password: `./ttctl init --config deploy.yml --ask-become-pass`.

Avoid passing passwords as command-line arguments because they can leak through shell history or process listings.

Plaintext `ansible.password` and `ansible.become_password` in `deploy.yml` remain supported only as a temporary local fallback. With `--vault`, even those config-provided values are moved into encrypted `vault.yml` instead of `vars.yml`.

## Rollback

See [ROLLBACK.md](ROLLBACK.md).

The important safety property is that this P1 change is additive: if `ttctl` does not work for your environment, continue using the existing `bootstrap.sh` flow.
