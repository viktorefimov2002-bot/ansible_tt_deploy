#!/bin/bash

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

install_sshpass_for_ansible() {
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Automatic sshpass installation is supported only on apt-based systems such as Ubuntu/Debian." >&2
    return 1
  fi

  echo "Installing sshpass for Ansible password authentication..." >&2
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
  SSHPASS_INSTALLED_BY_HELPER=true
  echo "sshpass was installed successfully." >&2
}

remove_sshpass_installed_by_helper() {
  if [ "${SSHPASS_INSTALLED_BY_HELPER:-false}" != "true" ]; then
    return 0
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    return 0
  fi

  echo "Removing sshpass installed by this helper..." >&2
  ensure_root_access
  if ! run_as_root apt-get remove -y sshpass >/tmp/trusttunnel-sshpass-remove.log 2>&1; then
    echo "Warning: failed to remove sshpass automatically. Please remove it manually if needed." >&2
    echo "See /tmp/trusttunnel-sshpass-remove.log for details." >&2
    return 1
  fi
  run_as_root apt-get autoremove -y >>/tmp/trusttunnel-sshpass-remove.log 2>&1 || true
}

detect_sshpass_need() {
  if [ "$NEEDS_SSHPASS" = "true" ] || [ "$NEEDS_SSHPASS" = "false" ]; then
    return 0
  fi

  if [ -f "${SCRIPT_DIR}/vars.yml" ] && grep -Eq '^ansible_password:' "${SCRIPT_DIR}/vars.yml"; then
    NEEDS_SSHPASS=true
    return 0
  fi

  if [ -f "${SCRIPT_DIR}/vars.yml" ] && grep -Eq "^trusttunnel_ssh_auth_method: '?password'?" "${SCRIPT_DIR}/vars.yml"; then
    NEEDS_SSHPASS=true
    return 0
  fi

  if [ -f "${SCRIPT_DIR}/vars.yml" ] && grep -Eq "^trusttunnel_ssh_auth_method: '?key'?" "${SCRIPT_DIR}/vars.yml"; then
    NEEDS_SSHPASS=false
    return 0
  fi

  if [ -f "${SCRIPT_DIR}/vault.yml" ]; then
    NEEDS_SSHPASS=true
  else
    NEEDS_SSHPASS=false
  fi
}

ensure_sshpass_for_ansible() {
  detect_sshpass_need
  if [ "$NEEDS_SSHPASS" != "true" ]; then
    return 0
  fi
  if command -v sshpass >/dev/null 2>&1; then
    return 0
  fi
  install_sshpass_for_ansible
}
