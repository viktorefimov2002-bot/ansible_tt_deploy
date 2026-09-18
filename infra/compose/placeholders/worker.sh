#!/bin/sh
set -eu
echo 'TTCP-003 worker placeholder: no jobs are consumed.'
trap 'kill "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; exit 0' TERM INT
while :; do
    sleep 3600 &
    child=$!
    wait "$child"
done
