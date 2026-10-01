#!/bin/sh
set -eu
# No production variables means the checked-in localhost configuration is used.
if [ -z "${TTCP_CLIENT_HOST:-}${TTCP_ADMIN_HOST:-}" ]; then exit 0; fi
valid_host() {
    printf '%s\n' "$1" | grep -Eq '^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$'
}
if ! valid_host "${TTCP_CLIENT_HOST:-}" || ! valid_host "${TTCP_ADMIN_HOST:-}"; then
    echo 'Invalid web origin hostname' >&2; exit 1
fi
case "$TTCP_CLIENT_HOST" in vpn.*) ;; *) echo 'Client host must be vpn.<domain>' >&2; exit 1 ;; esac
case "$TTCP_ADMIN_HOST" in admin.*) ;; *) echo 'Admin host must be admin.<domain>' >&2; exit 1 ;; esac
