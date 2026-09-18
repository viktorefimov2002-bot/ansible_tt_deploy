#!/bin/bash
# Integration test: isolated, disposable Docker Compose project, never ttcp-dev.
set -euo pipefail
docker compose version >/dev/null
docker info >/dev/null
COMPOSE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ttcp003.XXXXXXXX")
PROJECT="ttcp003-test-$(date +%s)-$$"
dc() { docker compose --project-name "$PROJECT" --env-file "$TEST_DIR/runtime.env" -f "$COMPOSE_DIR/compose.yaml" "$@"; }
cleanup() {
    dc down --volumes --remove-orphans >/dev/null || true
    rm -f -- "$TEST_DIR/runtime.env"
    rmdir -- "$TEST_DIR"
}
trap cleanup EXIT
umask 077
# Clear inherited overrides; generate disposable credentials, never print them.
unset POSTGRES_PASSWORD REDIS_PASSWORD GRAFANA_ADMIN_PASSWORD
unset POSTGRES_DB POSTGRES_USER GRAFANA_ADMIN_USER HTTP_PORT GRAFANA_PORT VM_RETENTION
touch "$TEST_DIR/runtime.env"
if dc config --quiet >/dev/null 2>&1; then
    echo 'FAIL: empty secrets accepted' >&2; exit 1
fi
for name in POSTGRES_PASSWORD REDIS_PASSWORD GRAFANA_ADMIN_PASSWORD; do
    value=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    printf '%s=%s\n' "$name" "$value" >> "$TEST_DIR/runtime.env"
done
unset value
printf 'HTTP_PORT=0\nGRAFANA_PORT=0\n' >> "$TEST_DIR/runtime.env"
dc config --quiet
dc up -d --wait --wait-timeout 180
dc exec -T nginx nginx -t
dc exec -T api nginx -t
sql() { dc exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At' ; }
printf 'CREATE TABLE runtime_probe (value text); INSERT INTO runtime_probe VALUES ($$persistent$$);\n' | sql
dc up -d --force-recreate --no-deps --wait --wait-timeout 120 postgres
test "$(printf 'SELECT value FROM runtime_probe;\n' | sql)" = persistent
if auth_error=$(dc exec -T postgres sh -c 'PGPASSWORD=incorrect psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1"' 2>&1); then
    echo 'FAIL: PostgreSQL accepted incorrect password' >&2; exit 1;
fi
printf '%s\n' "$auth_error" | grep -q 'password authentication failed'
test "$(dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping')" = PONG
auth_error=$(dc exec -T redis redis-cli ping 2>&1 || true)
printf '%s\n' "$auth_error" | grep -q NOAUTH
auth_error=$(dc exec -T redis sh -c 'REDISCLI_AUTH=incorrect redis-cli ping' 2>&1 || true)
printf '%s\n' "$auth_error" | grep -q WRONGPASS
dc exec -T victoriametrics wget -qO- http://127.0.0.1:8428/health
dc exec -T grafana wget -qO- http://victoriametrics:8428/health
dc exec -T grafana wget -qO- http://127.0.0.1:3000/api/health | grep -q '"database"[[:space:]]*:[[:space:]]*"ok"'
dc exec -T nginx wget -qO- http://127.0.0.1:8080/healthz
dc exec -T nginx sh -c 'wget -S -O /dev/null http://127.0.0.1:8080/api/example 2>&1 || true' | grep -q '503 Service'
dc ps
echo 'PASS: runtime health, placeholder routing, authentication, PostgreSQL persistence'
