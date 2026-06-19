#!/bin/bash
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INVENTORY_FILE="${SCRIPT_DIR}/inventory.ini"
VARS_FILE="${SCRIPT_DIR}/vars.yml"
VAULT_FILE="${SCRIPT_DIR}/vault.yml"
CREDENTIALS_FILES_DIR="${SCRIPT_DIR}/roles/trusttunnel_endpoint/files"
VAULT_PASSWORD_FILE=""

require_tty() {
  if [ ! -r /dev/tty ]; then
    echo "This bootstrap helper must be run from an interactive terminal." >&2
    echo "For non-interactive usage, copy vars.example.yml to vars.yml and run ansible-playbook manually." >&2
    exit 1
  fi
}

prompt() {
  label="$1"
  default="${2:-}"
  if [ -n "$default" ]; then
    printf "%s [%s]: " "$label" "$default" >&2
  else
    printf "%s: " "$label" >&2
  fi

  IFS= read -r value </dev/tty
  if [ -z "$value" ]; then
    value="$default"
  fi
  printf "%s" "$value"
}

prompt_secret() {
  label="$1"
  printf "%s: " "$label" >&2

  old_tty_settings=$(stty -g </dev/tty)
  trap 'stty "$old_tty_settings" </dev/tty' INT TERM
  stty -echo </dev/tty
  IFS= read -r value </dev/tty
  stty "$old_tty_settings" </dev/tty
  trap - INT TERM

  printf '\n' >&2
  printf "%s" "$value"
}

prompt_choice() {
  label="$1"
  default="$2"
  allowed="$3"

  while :; do
    value=$(prompt "$label" "$default")
    case " $allowed " in
      *" $value "*) printf "%s" "$value"; return 0 ;;
      *) printf "Invalid value '%s'. Allowed values: %s\n" "$value" "$allowed" >&2 ;;
    esac
  done
}

prompt_yes_no() {
  label="$1"
  default="$2"

  while :; do
    value=$(prompt "$label" "$default")
    case "$value" in
      yes|y|Y|да|д|Д) printf "yes"; return 0 ;;
      no|n|N|нет|н|Н) printf "no"; return 0 ;;
      *) printf "Invalid value '%s'. Allowed values: yes/no or да/нет\n" "$value" >&2 ;;
    esac
  done
}

run_as_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "This action requires root privileges, but sudo is not installed." >&2
    return 1
  fi
}

ensure_root_access() {
  if [ "$(id -u)" -eq 0 ]; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo -v
    return 0
  fi
  echo "This action requires root privileges, but sudo is not installed." >&2
  return 1
}

install_ansible() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic Ansible installation is supported only on apt-based systems such as Ubuntu/Debian." >&2
    return 1
  fi

  echo "Installing Ansible with apt-get..." >&2
  run_as_root apt-get update
  run_as_root apt-get install -y ansible
}

remove_ansible() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  echo "Removing Ansible installed by this bootstrap helper..." >&2
  if ! run_as_root apt-get remove -y ansible; then
    echo "Warning: failed to remove ansible automatically. Please remove it manually if needed." >&2
    return 1
  fi
  run_as_root apt-get autoremove -y || true
}

install_sshpass() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic sshpass installation is supported only on apt-based systems such as Ubuntu/Debian." >&2
    return 1
  fi

  echo "Installing sshpass with apt-get for Ansible password authentication..." >&2
  ensure_root_access
  if ! run_as_root apt-get update >/tmp/trusttunnel-sshpass-install.log 2>&1; then
    echo "Failed to update apt package lists while installing sshpass." >&2
    echo "See /tmp/trusttunnel-sshpass-install.log for details." >&2
    return 1
  fi
  if ! run_as_root apt-get install -y sshpass >>/tmp/trusttunnel-sshpass-install.log 2>&1; then
    echo "Failed to install sshpass." >&2
    echo "See /tmp/trusttunnel-sshpass-install.log for details." >&2
    return 1
  fi
  echo "sshpass was installed successfully." >&2
}

remove_sshpass() {
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  echo "Removing sshpass installed by this bootstrap helper..." >&2
  ensure_root_access
  if ! run_as_root apt-get remove -y sshpass >/tmp/trusttunnel-sshpass-remove.log 2>&1; then
    echo "Warning: failed to remove sshpass automatically. Please remove it manually if needed." >&2
    echo "See /tmp/trusttunnel-sshpass-remove.log for details." >&2
    return 1
  fi
  run_as_root apt-get autoremove -y >>/tmp/trusttunnel-sshpass-remove.log 2>&1 || true
}

yaml_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/''/g")"
}

write_vault_password_file() {
  vault_password="$1"
  VAULT_PASSWORD_FILE=$(mktemp /tmp/trusttunnel-vault-pass.XXXXXX)
  chmod 600 "$VAULT_PASSWORD_FILE"
  printf "%s\n" "$vault_password" > "$VAULT_PASSWORD_FILE"
}

encrypt_vault_file() {
  vault_password="$1"

  if ! command -v ansible-vault >/dev/null 2>&1; then
    echo "ansible-vault was not found in PATH. Cannot encrypt secrets." >&2
    return 1
  fi

  write_vault_password_file "$vault_password"
  ansible-vault encrypt "$VAULT_FILE" --vault-password-file "$VAULT_PASSWORD_FILE" >/dev/null
}

cleanup_dependencies() {
  if [ -n "${VAULT_PASSWORD_FILE:-}" ] && [ -f "$VAULT_PASSWORD_FILE" ]; then
    rm -f "$VAULT_PASSWORD_FILE"
  fi

  if [ "${ansible_installed_by_bootstrap:-false}" = "true" ]; then
    remove_ansible || true
  fi

  if [ "${sshpass_installed_by_bootstrap:-false}" = "true" ]; then
    remove_sshpass || true
  fi
}

require_tty

ansible_installed_by_bootstrap=false
sshpass_installed_by_bootstrap=false
trap cleanup_dependencies EXIT
ansible_present=$(prompt_yes_no "Is ansible/ansible-playbook already installed on this machine? yes/no" "yes")
if [ "$ansible_present" = "yes" ]; then
  if ! command -v ansible-playbook >/dev/null 2>&1; then
    echo "ansible-playbook was not found in PATH. Install Ansible first or rerun and answer 'no' to let bootstrap install it." >&2
    exit 1
  fi
else
  if command -v ansible-playbook >/dev/null 2>&1; then
    echo "ansible-playbook is already available in PATH; bootstrap will use it and will not remove it afterwards." >&2
  else
    install_allowed=$(prompt_yes_no "Can bootstrap install Ansible temporarily for this deployment and remove it afterwards? yes/no" "yes")
    if [ "$install_allowed" = "no" ]; then
      echo "Cannot continue: this deployment requires ansible-playbook on the control machine." >&2
      exit 1
    fi

    install_ansible
    ansible_installed_by_bootstrap=true

    if ! command -v ansible-playbook >/dev/null 2>&1; then
      echo "Ansible installation finished, but ansible-playbook is still not available in PATH." >&2
      exit 1
    fi
  fi
fi

server_host=$(prompt "Remote server IP or DNS name for SSH/Ansible")
ssh_user=$(prompt "Remote SSH user for Ansible" "root")
ssh_auth_method=$(prompt_choice "SSH authentication method for Ansible: password, key" "password" "password key")
ssh_password=""
become_password=""
if [ "$ssh_auth_method" = "password" ]; then
  ssh_password=$(prompt_secret "Remote SSH password for Ansible")
  if ! command -v sshpass >/dev/null 2>&1; then
    sshpass_allowed=$(prompt_yes_no "Ansible password SSH requires sshpass. Can bootstrap install it temporarily and remove it afterwards? yes/no" "yes")
    if [ "$sshpass_allowed" = "no" ]; then
      echo "Cannot continue: password-based Ansible SSH requires sshpass on the control machine." >&2
      exit 1
    fi
    install_sshpass
    sshpass_installed_by_bootstrap=true
  fi
fi

if [ "$ssh_user" != "root" ]; then
  become_password_required=$(prompt_yes_no "Does sudo/become on the remote server require a password? yes/no" "no")
  if [ "$become_password_required" = "yes" ]; then
    become_password=$(prompt_secret "Remote sudo/become password")
    if [ -z "$become_password" ] && [ -n "$ssh_password" ]; then
      become_password="$ssh_password"
    fi
  fi
fi

domain=$(prompt "TrustTunnel domain for TLS certificate/SNI, for example vpn.example.com" "$server_host")
public_address=$(prompt "Public address written to client configs; clients connect to it. Use domain/IP, optionally with port, for example vpn.example.com or vpn.example.com:443" "${domain}:443")
cert_mode=$(prompt_choice "Certificate mode: letsencrypt, selfsigned, existing" "letsencrypt" "letsencrypt selfsigned existing")
acme_email=""
if [ "$cert_mode" = "letsencrypt" ]; then
  acme_email=$(prompt "Let's Encrypt email")
fi
use_existing_credentials=$(prompt_yes_no "Use an existing TrustTunnel credentials.toml to preserve VPN clients? yes/no" "no")
existing_credentials_file=""
vpn_username=""
vpn_password=""
if [ "$use_existing_credentials" = "yes" ]; then
  mkdir -p "$CREDENTIALS_FILES_DIR"
  echo "Place the existing credentials TOML under:" >&2
  echo "  $CREDENTIALS_FILES_DIR" >&2
  existing_credentials_file=$(prompt "Credentials file name inside role files directory" "credentials.toml")
  if [ ! -f "$CREDENTIALS_FILES_DIR/$existing_credentials_file" ]; then
    echo "Credentials file not found: $CREDENTIALS_FILES_DIR/$existing_credentials_file" >&2
    echo "Put the file there and rerun bootstrap.sh." >&2
    exit 1
  fi
else
  vpn_username=$(prompt "VPN client username to create in credentials.toml" "user1")
  vpn_password=$(prompt_secret "VPN client password to create in credentials.toml")
fi
open_firewall=$(prompt_choice "Open local firewall ports with Ansible? true/false" "false" "true false")
firewall_backend="auto"
if [ "$open_firewall" = "true" ]; then
  echo "Firewall backend will be auto-detected on the remote server: ufw first (Ubuntu default), then firewalld, otherwise none." >&2
fi

vault_enabled=false
has_secrets=false
if [ -n "$ssh_password" ] || [ -n "$become_password" ] || { [ "$use_existing_credentials" = "no" ] && [ -n "$vpn_password" ]; }; then
  has_secrets=true
fi

if [ "$has_secrets" = "true" ]; then
  use_vault=$(prompt_yes_no "Encrypt generated secrets with Ansible Vault? yes/no" "yes")
  if [ "$use_vault" = "yes" ]; then
    vault_password=$(prompt_secret "Ansible Vault password for vault.yml")
    if [ -z "$vault_password" ]; then
      echo "Cannot continue: Ansible Vault password cannot be empty." >&2
      exit 1
    fi
    vault_enabled=true
  else
    echo "Warning: SSH/VPN passwords will be written in plaintext to $VARS_FILE with mode 600." >&2
  fi
fi

cat > "$INVENTORY_FILE" <<EOF
[trusttunnel]
${server_host} ansible_user=${ssh_user}
EOF

cat > "$VARS_FILE" <<EOF
---
trusttunnel_domain: $(yaml_quote "$domain")
trusttunnel_public_address: $(yaml_quote "$public_address")
trusttunnel_cert_mode: $(yaml_quote "$cert_mode")
trusttunnel_acme_email: $(yaml_quote "$acme_email")
trusttunnel_open_firewall: ${open_firewall}
trusttunnel_firewall_backend: $(yaml_quote "$firewall_backend")
trusttunnel_existing_credentials_file: $(yaml_quote "$existing_credentials_file")
EOF

if [ "$use_existing_credentials" = "no" ] && [ "$vault_enabled" = "false" ]; then
  cat >> "$VARS_FILE" <<EOF
trusttunnel_client_username: $(yaml_quote "$vpn_username")
trusttunnel_client_password: $(yaml_quote "$vpn_password")
trusttunnel_clients:
  - username: $(yaml_quote "$vpn_username")
    password: $(yaml_quote "$vpn_password")
EOF
else
  cat >> "$VARS_FILE" <<EOF
trusttunnel_clients: []
EOF
fi

if [ "$vault_enabled" = "true" ]; then
  cat > "$VAULT_FILE" <<EOF
---
EOF

  if [ "$use_existing_credentials" = "no" ]; then
    cat >> "$VAULT_FILE" <<EOF
trusttunnel_client_username: $(yaml_quote "$vpn_username")
trusttunnel_client_password: $(yaml_quote "$vpn_password")
trusttunnel_clients:
  - username: $(yaml_quote "$vpn_username")
    password: $(yaml_quote "$vpn_password")
EOF
  fi

  if [ -n "$ssh_password" ]; then
    {
      echo "ansible_password: $(yaml_quote "$ssh_password")"
    } >> "$VAULT_FILE"
  fi

  if [ -n "$become_password" ]; then
    {
      echo "ansible_become_password: $(yaml_quote "$become_password")"
    } >> "$VAULT_FILE"
  fi

  chmod 600 "$VAULT_FILE"
  encrypt_vault_file "$vault_password"
fi

if [ "$vault_enabled" = "false" ] && [ -n "$ssh_password" ]; then
  {
    echo "ansible_password: $(yaml_quote "$ssh_password")"
  } >> "$VARS_FILE"
fi

if [ "$vault_enabled" = "false" ] && [ -n "$become_password" ]; then
  {
    echo "ansible_become_password: $(yaml_quote "$become_password")"
  } >> "$VARS_FILE"
fi

chmod 600 "$VARS_FILE"
chmod 600 "$INVENTORY_FILE"

echo
echo "Created:"
echo "  $INVENTORY_FILE"
echo "  $VARS_FILE"
if [ "$vault_enabled" = "true" ]; then
  echo "  $VAULT_FILE"
fi
echo
ansible_result=0
if [ "$vault_enabled" = "true" ]; then
  ansible-playbook -i "$INVENTORY_FILE" "$SCRIPT_DIR/site.yml" -e "@$VARS_FILE" -e "@$VAULT_FILE" --vault-password-file "$VAULT_PASSWORD_FILE" || ansible_result=$?
else
  ansible-playbook -i "$INVENTORY_FILE" "$SCRIPT_DIR/site.yml" -e "@$VARS_FILE" || ansible_result=$?
fi

exit "$ansible_result"
