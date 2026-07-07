# Rollback guide

This repository keeps the P1 usability changes isolated behind `ttctl` and `deploy.example.yml`.
The existing `bootstrap.sh`, `deploy.sh`, `uninstall.sh`, Ansible role and playbooks remain the primary deployment path.

## If the P1 branch was not merged

Do nothing. The `main` branch is unchanged.

To remove the branch locally after testing:

```bash
git checkout main
git branch -D p1-usability-safe-cli
```

To remove the remote branch:

```bash
git push origin --delete p1-usability-safe-cli
```

## If the P1 branch was merged and you want to revert it

Find the merge commit and revert it:

```bash
git checkout main
git pull
git log --oneline
# copy the merge commit SHA
git revert -m 1 <merge-commit-sha>
git push
```

If the merge was a squash merge, revert the single squash commit instead:

```bash
git revert <squash-commit-sha>
git push
```

## If only generated local runtime files should be removed

`ttctl init` generates local runtime files that are ignored by git:

```bash
rm -f inventory.ini vars.yml vault.yml deploy.sh uninstall.sh
rm -rf client_configs
```

If you imported existing TrustTunnel credentials for testing, remove the copied local file too:

```bash
rm -f roles/trusttunnel_endpoint/files/credentials.toml
```

## Server-side rollback

If a deployment reached the server and you want to remove TrustTunnel from the target host, use the existing uninstall path:

```bash
./uninstall.sh
```

or:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e @vars.yml
```

For full test cleanup, including local firewall changes and Let's Encrypt files when you intentionally want that:

```bash
ansible-playbook -i inventory.ini uninstall.yml -e @vars.yml \
  -e trusttunnel_remove_letsencrypt_cert=true \
  -e trusttunnel_close_firewall=true
```
