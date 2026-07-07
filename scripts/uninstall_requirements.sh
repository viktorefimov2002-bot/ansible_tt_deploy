#!/bin/bash
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
DEFAULT_VENV_DIR="${PROJECT_DIR}/.venv"

ASSUME_YES="false"
DRY_RUN="false"
REMOVE_VENV="false"
VENV_DIR="$DEFAULT_VENV_DIR"

usage() {
  cat <<'EOF'
Usage:
  ./scripts/uninstall_requirements.sh [options]

Options:
  --dry-run            Show what would be removed, but do not remove anything.
  -y, --yes            Do not ask before removing selected items.
  --venv               Offer/remove the project .venv directory.
  --venv-dir PATH      Offer/remove a custom virtualenv directory.
  -h, --help           Show this help.

This script is intentionally conservative.
It never removes everything automatically. It asks separately about each item:
  - ansible
  - sshpass
  - python3-pip / python-pip
  - PyYAML from the current Python environment
  - optional project virtualenv

It does not remove python3 by default because Python is often required by the OS
and by other tools.
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
  default="${2:-no}"

  if [ "$ASSUME_YES" = "true" ]; then
    case "$default" in
      yes) return 0 ;;
      *) return 1 ;;
    esac
  fi

  if [ ! -r /dev/tty ]; then
    return 1
  fi

  while :; do
    printf "%s [%s]: " "$label" "$default" >&2
    IFS= read -r answer </dev/tty
    if [ -z "$answer" ]; then
      answer="$default"
    fi
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

remove_package() {
  package_name="$1"
  manager=$(package_manager)

  if [ "$DRY_RUN" = "true" ]; then
    echo "Would remove package: $package_name" >&2
    return 0
  fi

  case "$manager" in
    apt)
      run_sudo apt-get remove -y "$package_name"
      ;;
    dnf)
      run_sudo dnf remove -y "$package_name"
      ;;
    yum)
      run_sudo yum remove -y "$package_name"
      ;;
    pacman)
      run_sudo pacman -Rns --noconfirm "$package_name"
      ;;
    zypper)
      run_sudo zypper --non-interactive remove "$package_name"
      ;;
    *)
      fail "unsupported package manager. Remove $package_name manually if needed."
      ;;
  esac
}

pip_uninstall() {
  package_name="$1"
  if ! have_cmd python3; then
    echo "python3 not found; skipping Python package removal" >&2
    return 0
  fi

  if [ "$DRY_RUN" = "true" ]; then
    echo "Would run: python3 -m pip uninstall -y $package_name" >&2
    return 0
  fi

  python3 -m pip uninstall -y "$package_name" || true
}

remove_dir() {
  dir_path="$1"
  [ -d "$dir_path" ] || return 0

  if [ "$DRY_RUN" = "true" ]; then
    echo "Would remove directory: $dir_path" >&2
    return 0
  fi

  rm -rf "$dir_path"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -y|--yes)
      ASSUME_YES="true"
      shift
      ;;
    --venv)
      REMOVE_VENV="true"
      shift
      ;;
    --venv-dir)
      [ "$#" -ge 2 ] || fail "--venv-dir requires a path"
      REMOVE_VENV="true"
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

info "Safe dependency removal helper"
echo "This script will ask before removing each item." >&2
echo "It will not remove python3 automatically." >&2

if confirm "Remove ansible package?" "no"; then
  remove_package ansible
fi

if confirm "Remove sshpass package?" "no"; then
  remove_package sshpass
fi

manager=$(package_manager)
case "$manager" in
  pacman) pip_pkg="python-pip" ;;
  *) pip_pkg="python3-pip" ;;
esac

if confirm "Remove ${pip_pkg} package?" "no"; then
  remove_package "$pip_pkg"
fi

if confirm "Uninstall PyYAML from the current python3 environment?" "no"; then
  pip_uninstall PyYAML
fi

if [ "$REMOVE_VENV" = "true" ] || [ -d "$VENV_DIR" ]; then
  if confirm "Remove virtualenv directory ${VENV_DIR}?" "no"; then
    remove_dir "$VENV_DIR"
  fi
fi

info "Dependency removal helper completed"
