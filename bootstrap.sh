#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INVENTORY_FILE="${SCRIPT_DIR}/inventory.ini"
VARS_FILE="${SCRIPT_DIR}/vars.yml"

prompt() {
  label="$1"
  default="${2:-}"
  if [ -n "$default" ]; then
    printf "%s [%s]: " "$label" "$default"
  else
    printf "%s: " "$label"
  fi
  read -r value
  if [ -z "$value" ]; then
    value="$default"
  fi
  printf "%s" "$value"
}

prompt_secret() {
  label="$1"
  printf "%s: " "$label"
  stty -echo
  read -r value
  stty echo
  printf "\n" >&2
  printf "%s" "$value"
}

yaml_quote() {
  printf "'%s'" "$(printf "%s" "$1" | sed "s/'/''/g")"
}

if ! command -v ansible-playbook >/dev/null 2>&1; then
  echo "ansible-playbook is not installed or is not in PATH." >&2
  exit 1
fi

server_host=$(prompt "Server IP or DNS name")
ssh_user=$(prompt "SSH user" "root")
domain=$(prompt "TrustTunnel domain" "$server_host")
public_address=$(prompt "Public client address" "${domain}:443")
cert_mode=$(prompt "Certificate mode: letsencrypt, selfsigned, existing" "letsencrypt")
acme_email=""
if [ "$cert_mode" = "letsencrypt" ]; then
  acme_email=$(prompt "Let's Encrypt email")
fi
vpn_username=$(prompt "VPN username" "user1")
vpn_password=$(prompt_secret "VPN password")
open_firewall=$(prompt "Open local firewall ports with Ansible? true/false" "false")
firewall_backend=$(prompt "Firewall backend: firewalld, ufw, none" "firewalld")

cat > "$INVENTORY_FILE" <<EOF
[trusttunnel]
${server_host} ansible_user=${ssh_user}
EOF

cat > "$VARS_FILE" <<EOF
---
trusttunnel_domain: $(yaml_quote "$domain")
trusttunnel_public_address: $(yaml_quote "$public_address")
trusttunnel_client_username: $(yaml_quote "$vpn_username")
trusttunnel_client_password: $(yaml_quote "$vpn_password")
trusttunnel_cert_mode: $(yaml_quote "$cert_mode")
trusttunnel_acme_email: $(yaml_quote "$acme_email")
trusttunnel_open_firewall: ${open_firewall}
trusttunnel_firewall_backend: $(yaml_quote "$firewall_backend")
trusttunnel_clients:
  - username: $(yaml_quote "$vpn_username")
    password: $(yaml_quote "$vpn_password")
EOF

chmod 600 "$VARS_FILE"

echo
echo "Created:"
echo "  $INVENTORY_FILE"
echo "  $VARS_FILE"
echo
ansible-playbook -i "$INVENTORY_FILE" "$SCRIPT_DIR/site.yml" -e "@$VARS_FILE"
