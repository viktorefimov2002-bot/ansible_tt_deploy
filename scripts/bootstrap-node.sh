#!/bin/sh
# Run the reviewed repository copy locally on the node, never with shell tracing.
set +x
set -eu
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
if [ "$#" -ne 0 ]; then
    echo 'bootstrap: no arguments accepted; supply the public key on stdin' >&2
    exit 1
fi
if [ "$(id -u)" -ne 0 ] || [ "$(uname -s)" != Linux ]; then
    echo 'bootstrap: run locally as root on supported Linux' >&2
    exit 1
fi
# Validate platform before installing even the interpreter prerequisite.
. /etc/os-release
case "$ID:$VERSION_ID:$(uname -m)" in
    ubuntu:22.04:x86_64|ubuntu:22.04:aarch64|ubuntu:24.04:x86_64|ubuntu:24.04:aarch64|debian:12:x86_64|debian:12:aarch64|debian:13:x86_64|debian:13:aarch64) ;;
    *) echo 'bootstrap: unsupported OS or CPU architecture' >&2; exit 1 ;;
esac
if ! command -v python3 >/dev/null 2>&1; then
    if ! apt-get update </dev/null >/dev/null 2>&1 ||
       ! DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 </dev/null >/dev/null 2>&1; then
        echo 'bootstrap: Python prerequisite installation failed' >&2
        exit 1
    fi
fi
exec python3 -I "$(dirname "$0")/bootstrap_node.py"
