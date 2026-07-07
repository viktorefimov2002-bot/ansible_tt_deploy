#!/bin/bash
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
REQUIREMENTS_FILE="${PROJECT_DIR}/requirements.txt"

ASSUME_YES="false"
CHECK_ONLY="false"
INSTALL_SYSTEM="true"
INSTALL_PYTHON="true"
USE_VENV="false"
VENV_DIR="${PROJECT_DIR}/.venv"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/install_requirements.sh [options]

Options:
  --check-only          Only check dependencies, do not install anything.
  -y, --yes            Do not ask before installing packages.
  --system-only        Install/check only system packages.
  --python-only        Install/check only Python requirements.
  --venv               Install Python requirements into .venv.
  --venv-dir PATH      Install Python requirements into PATH.
  -h, --help           Show this help.

What this script handles:
  - system tools: python3, python3-pip, ansible, sshpass
  - Python libraries from requirements.txt: PyYAML

Supported package managers:
  - apt-get
  - dnf
  - yum
  - pacman
  - zypper

Notes:
  - ansible-playbook is provided by the ansible package on common Linux distributions.
  - sshpass is needed only for SSH password auth. SSH key users may not need it.
EOF
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

info() {
  echo "==> $*" >&2
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

confirm() {
  label="$1"
  if [ "$ASSUME_YES" = "true" ]; then
    return 0
  fi
  if [ ! -r /dev/tty ]; then
    return 1
  fi
  while :; do
    printf "%s [yes/no]: " "$label" >&2
    IFS= read -r answer </dev/tty
    case "$answer" in
      yes|y|Y|да|д|Д) return 0 ;;
      no|n|N|нет|н|Н) return 1 ;;
      *) echo "Please answer yes/no." >&2 ;;
    esac
  done
}

run_sudo() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  else
    sudo "$@"
  fi
}

package_manager() {
  if have_cmd apt-get; then
    echo apt
  elif have_cmd dnf; then
    echo dnf
  elif have_cmd yum; then
    echo yum
  elif have_cmd pacman; then
    echo pacman
  elif have_cmd zypper; then
    echo zypper
  else
    echo unknown
  fi
}

system_packages_for_manager() {
  manager="$1"
  case "$manager" in
    apt)
      echo "python3 python3-pip ansible sshpass"
      ;;
    dnf|yum)
      echo "python3 python3-pip ansible sshpass"
      ;;
    pacman)
      echo "python python-pip ansible sshpass"
      ;;
    zypper)
      echo "python3 python3-pip ansible sshpass"
      ;;
    *)
      echo ""
      ;;
  esac
}

missing_commands() {
  missing=""
  for cmd in python3 ansible ansible-playbook; do
    if ! have_cmd "$cmd"; then
      missing="${missing} ${cmd}"
    fi
  done
  if ! have_cmd sshpass; then
    missing="${missing} sshpass"
  fi
  echo "$missing"
}

check_python_yaml() {
  if have_cmd python3 && python3 - <<'PY' >/dev/null 2>&1
import yaml
PY
  then
    return 0
  fi
  return 1
}

install_system_packages() {
  manager=$(package_manager)
  packages=$(system_packages_for_manager "$manager")
  [ -n "$packages" ] || fail "unsupported package manager. Install python3, python3-pip, ansible and sshpass manually."

  info "Detected package manager: $manager"
  info "System packages to install/check: $packages"

  if [ "$CHECK_ONLY" = "true" ]; then
    return 0
  fi

  confirm "Install system packages using ${manager}?" || fail "system package installation cancelled"

  case "$manager" in
    apt)
      run_sudo apt-get update
      run_sudo apt-get install -y $packages
      ;;
    dnf)
      run_sudo dnf install -y $packages
      ;;
    yum)
      run_sudo yum install -y $packages
      ;;
    pacman)
      run_sudo pacman -Sy --needed --noconfirm $packages
      ;;
    zypper)
      run_sudo zypper --non-interactive install $packages
      ;;
  esac
}

install_python_requirements() {
  require_file="$REQUIREMENTS_FILE"
  [ -f "$require_file" ] || fail "requirements.txt not found: $require_file"

  if [ "$CHECK_ONLY" = "true" ]; then
    return 0
  fi

  if [ "$USE_VENV" = "true" ]; then
    have_cmd python3 || fail "python3 is required to create a venv"
    info "Creating/updating virtual environment: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    "${VENV_DIR}/bin/python" -m pip install --upgrade pip
    "${VENV_DIR}/bin/python" -m pip install -r "$require_file"
  else
    have_cmd python3 || fail "python3 is required to install Python requirements"
    info "Installing Python requirements for the current Python environment"
    python3 -m pip install -r "$require_file"
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check-only)
      CHECK_ONLY="true"
      shift
      ;;
    -y|--yes)
      ASSUME_YES="true"
      shift
      ;;
    --system-only)
      INSTALL_PYTHON="false"
      shift
      ;;
    --python-only)
      INSTALL_SYSTEM="false"
      shift
      ;;
    --venv)
      USE_VENV="true"
      shift
      ;;
    --venv-dir)
      [ "$#" -ge 2 ] || fail "--venv-dir requires a path"
      USE_VENV="true"
      VENV_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

info "Checking command dependencies"
missing=$(missing_commands)
if [ -n "$missing" ]; then
  echo "Missing commands:${missing}" >&2
  if [ "$INSTALL_SYSTEM" = "true" ]; then
    install_system_packages
  elif [ "$CHECK_ONLY" = "true" ]; then
    exit 1
  fi
else
  echo "All required commands are present: python3, ansible, ansible-playbook, sshpass" >&2
fi

if check_python_yaml; then
  echo "Python module is present: yaml" >&2
else
  echo "Missing Python module: yaml" >&2
  if [ "$INSTALL_PYTHON" = "true" ]; then
    install_python_requirements
  elif [ "$CHECK_ONLY" = "true" ]; then
    exit 1
  fi
fi

info "Dependency check completed"
