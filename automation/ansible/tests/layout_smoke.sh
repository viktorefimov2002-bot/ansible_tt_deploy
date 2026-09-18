#!/bin/bash
# No server access: exercise real entry points with an Ansible command recorder.
set -euo pipefail
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ttcp002.XXXXXXXX")
cleanup() {
  case "$TEST_DIR" in
    "${TMPDIR:-/tmp}"/ttcp002.*) rm -rf -- "$TEST_DIR" ;;
    *) echo "Refusing cleanup outside test directory" >&2; return 1 ;;
  esac
}
trap cleanup EXIT
ROOT="$TEST_DIR/checkout with spaces"
mkdir -p "$ROOT/automation" "$ROOT/scripts" "$ROOT/helpers" "$TEST_DIR/bin"
cp -R "$REPO_DIR/automation/ansible" "$ROOT/automation/"
for file in ttctl bootstrap.sh add_vpn_clients.sh remove_vpn_clients.sh; do
  cp "$REPO_DIR/$file" "$ROOT/$file"
done
cp "$REPO_DIR/scripts/"*.sh "$ROOT/scripts/"
cp "$REPO_DIR/helpers/ansible_playbook_helper.sh" "$ROOT/helpers/"
for file in site.yml uninstall.yml status.yml add_clients.yml remove_clients.yml; do
  cp "$REPO_DIR/$file" "$ROOT/$file"
done
find "$ROOT" -type f \( -name '*.sh' -o -name ttctl \) -print0 |
  while IFS= read -r -d '' file; do bash -n "$file"; done
export TRACE="$TEST_DIR/trace"
cat > "$TEST_DIR/bin/ansible-playbook" <<'SH'
#!/bin/bash
printf '%s\n' "$PWD" "$@" > "$TRACE"
for arg in "$@"; do
  case "$arg" in
    trusttunnel_status_output_file=*) printf 'status fixture\n' > "${arg#*=}" ;;
  esac
done
exit "${MOCK_EXIT:-0}"
SH
chmod +x "$TEST_DIR/bin/ansible-playbook"
export PATH="$TEST_DIR/bin:$PATH"
printf '[trusttunnel]\nfixture.invalid\n' > "$ROOT/inventory.ini"
printf 'trusttunnel_ssh_auth_method: key\n' > "$ROOT/vars.yml"
cd "$TEST_DIR"
if command -v python3 >/dev/null 2>&1 && python3 -c 'import yaml' 2>/dev/null; then
  cp "$ROOT/automation/ansible/deploy.example.yml" "$TEST_DIR/deploy config.yml"
  bash "$ROOT/ttctl" init --config 'deploy config.yml' >/dev/null
  grep -Fx '203.0.113.10 ansible_user=root' "$ROOT/inventory.ini" >/dev/null
  grep -Fx 'trusttunnel_domain: vpn.example.com' "$ROOT/vars.yml" >/dev/null
  # Restore the explicit key fixture for sshpass detection below.
  printf 'trusttunnel_ssh_auth_method: key\n' >> "$ROOT/vars.yml"
else
  echo 'SKIP: ttctl init requires python3 and PyYAML'
fi
assert_trace() { grep -Fx -- "$1" "$TRACE" >/dev/null; }
for entry in "$ROOT/ttctl" "$ROOT/automation/ansible/ttctl"; do
  bash "$entry" --help >/dev/null
  bash "$entry" deploy -e 'marker=two words' --check
  assert_trace "$TEST_DIR"
  assert_trace "$ROOT/inventory.ini"
  assert_trace "$ROOT/automation/ansible/site.yml"
  assert_trace "@$ROOT/vars.yml"
  assert_trace 'marker=two words'
  assert_trace --check
  bash "$entry" uninstall --check
  assert_trace "$ROOT/automation/ansible/uninstall.yml"
  [ "$(bash "$entry" status --short)" = 'status fixture' ]
  assert_trace "$ROOT/automation/ansible/status.yml"
  assert_trace 'trusttunnel_status_mode=short'
done
if MOCK_EXIT=17 bash "$ROOT/ttctl" deploy; then
  echo 'CLI lost Ansible failure status' >&2; exit 1
else
  [ "$?" -eq 17 ]
fi
FILES="$ROOT/automation/ansible/roles/trusttunnel_endpoint/files"
printf '[[client]]\nusername = "fixture"\npassword = "test-only"\n' > "$TEST_DIR/source.toml"
bash "$ROOT/ttctl" import-credentials source.toml >/dev/null
cmp "$TEST_DIR/source.toml" "$FILES/credentials.toml"
cp "$TEST_DIR/source.toml" "$FILES/new_clients.toml"
printf 'fixture\n' > "$FILES/remove_clients.txt"
bash "$ROOT/ttctl" add-client --no-apply --no-sync-from-server
assert_trace "$ROOT/automation/ansible/add_clients.yml"
assert_trace "trusttunnel_new_clients_file=$FILES/new_clients.toml"
assert_trace 'trusttunnel_apply_to_server=false'
bash "$ROOT/ttctl" remove-client --no-apply --no-sync-from-server
assert_trace "$ROOT/automation/ansible/remove_clients.yml"
assert_trace "trusttunnel_remove_clients_file=$FILES/remove_clients.txt"
bash "$ROOT/ttctl" add-client --no-apply source.toml 'custom credentials.toml'
assert_trace 'trusttunnel_new_clients_file=source.toml'
assert_trace 'trusttunnel_credentials_local_file=custom credentials.toml'
# Exercise the local editing helpers through their old entry points.
TRUSTTUNNEL_DEPLOY_AFTER_REMOVE=no bash "$ROOT/remove_vpn_clients.sh" >/dev/null 2>&1
! grep -q 'username = "fixture"' "$FILES/credentials.toml"
TRUSTTUNNEL_DEPLOY_AFTER_ADD=no bash "$ROOT/add_vpn_clients.sh" >/dev/null
grep -q 'username = "fixture"' "$FILES/credentials.toml"
for script in install_requirements uninstall_requirements; do
  bash "$ROOT/scripts/$script.sh" --help >/dev/null
done
# Source the old helper path just as pre-move generated scripts do.
SCRIPT_DIR="$ROOT"
NEEDS_SSHPASS=auto
. "$ROOT/helpers/ansible_playbook_helper.sh"
detect_sshpass_need
[ "$NEEDS_SSHPASS" = false ]
# Exercise the actual bootstrap helper generator without interactive provisioning.
eval "$(sed -n '/^write_playbook_helper() {/,/^write_playbook_helper "\$DEPLOY_HELPER_FILE"/p' "$ROOT/automation/ansible/bootstrap.sh" | sed '$d')"
write_playbook_helper "$ROOT/deploy.sh" site.yml
write_playbook_helper "$ROOT/uninstall.sh" uninstall.yml
chmod +x "$ROOT/deploy.sh" "$ROOT/uninstall.sh"
bash -n "$ROOT/deploy.sh"
bash -n "$ROOT/uninstall.sh"
TRUSTTUNNEL_NEEDS_SSHPASS=false bash "$ROOT/ttctl" deploy --check
assert_trace "$ROOT/automation/ansible/site.yml"
TRUSTTUNNEL_NEEDS_SSHPASS=false bash "$ROOT/ttctl" uninstall --check
assert_trace "$ROOT/automation/ansible/uninstall.yml"
# Pre-move generated helpers reference root playbooks and the root helper shim.
sed 's@/automation/ansible/@/@g' "$ROOT/deploy.sh" > "$ROOT/legacy.sh"
TRUSTTUNNEL_NEEDS_SSHPASS=false bash "$ROOT/legacy.sh" --check
assert_trace "$ROOT/site.yml"
echo 'PASS: shell syntax, CLI routing, arguments, exit status, local credentials, generated helpers'
