#!/bin/bash
# Compatibility entry point; keep the caller working directory and arguments.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec bash "${SCRIPT_DIR}/automation/ansible/bootstrap.sh" "$@"
