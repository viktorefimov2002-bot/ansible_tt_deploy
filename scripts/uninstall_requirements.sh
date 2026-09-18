#!/bin/bash
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec bash "${SCRIPT_DIR}/../automation/ansible/scripts/uninstall_requirements.sh" "$@"
