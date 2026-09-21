#!/bin/bash
# Integration test: isolated, disposable Docker Compose project, never ttcp-dev.
set -euo pipefail
docker compose version >/dev/null
docker info >/dev/null
COMPOSE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/ttcp004.XXXXXXXX")
PROJECT="ttcp004-test-$(date +%s)-$$"
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
unset POSTGRES_APP_USER POSTGRES_APP_PASSWORD
unset TTCP_LOG_LEVEL TTCP_AUTH_ENCRYPTION_KEY TTCP_AUTH_SESSION_SECONDS
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
dc up -d --wait --wait-timeout 180 postgres redis
dc build api worker migrate
# Explicit deployment step, never performed automatically by API/worker startup.
dc run --rm --no-deps migrate
dc run --rm --no-deps migrate
dc run --rm --no-deps migrate python -m alembic check
dc up -d --build --wait --wait-timeout 180
dc exec -T nginx nginx -t
dc exec -T api python -m apps.shared.healthcheck api
dc exec -T worker python -m apps.shared.healthcheck worker
sql() { dc exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At' ; }

# Use only this disposable project's database/Redis. No public job creation API.
create_job() {
    printf "INSERT INTO jobs(type,target_type,request_id,idempotency_key,replay_safe,cancellable) VALUES ('internal.noop','internal','compose-smoke',gen_random_uuid()::text,true,true) RETURNING id;\n" | sql | sed -n '1p'
}
wait_job() {
    for attempt in $(seq 1 30); do
        state=$(printf "SELECT status FROM jobs WHERE id='%s';\n" "$1" | sql)
        if [ "$state" = succeeded ]; then return; fi
        if [ "$state" = failed ]; then echo 'FAIL: internal job failed' >&2; exit 1; fi
        sleep 1
    done
    echo 'FAIL: durable job did not complete' >&2; exit 1
}
job_id=$(create_job)
wait_job "$job_id"
dc stop worker
job_id=$(create_job)
dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli LPUSH ttcp:jobs:queue "$1"' sh "$job_id" >/dev/null
dc up -d --force-recreate --no-deps --wait --wait-timeout 120 redis
dc up -d --wait --wait-timeout 120 worker
wait_job "$job_id"
test "$(printf "SELECT attempts FROM jobs WHERE id='%s';\n" "$job_id" | sql)" = 1
test "$(dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli XLEN "ttcp:jobs:log:$1"' sh "$job_id")" -gt 0
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
dc exec -T nginx wget -qO- http://127.0.0.1:8080/api/healthz | grep -q '"ok"'
dc exec -T nginx wget -qO- http://127.0.0.1:8080/api/readyz | grep -q '"ready"'
dc exec -T nginx sh -c 'wget -S -O /dev/null http://127.0.0.1:8080/api/example 2>&1 || true' | grep -q '404 Not Found'

# Runtime failures must not turn liveness into a restart loop; readiness recovers.
for dependency in redis postgres; do
    dc stop "$dependency"
    dc exec -T nginx wget -qO- http://api:8080/healthz | grep -q '"ok"'
    dc exec -T nginx sh -c 'wget -S -O /dev/null http://api:8080/readyz 2>&1 || true' | grep -q '503 Service'
    if dc exec -T worker python -m apps.shared.healthcheck worker; then
        echo "FAIL: worker probe accepted unavailable $dependency" >&2; exit 1
    fi
    if dc run --rm --no-deps worker; then
        echo "FAIL: worker started without $dependency" >&2; exit 1
    fi
    if dc run --rm --no-deps api; then
        echo "FAIL: API started without $dependency" >&2; exit 1
    fi
    dc up -d --wait --wait-timeout 120 "$dependency"
    dc up -d --wait --wait-timeout 120 api worker
    dc exec -T nginx wget -qO- http://api:8080/readyz | grep -q '"ready"'
done

for service in api worker; do
    if dc run --rm --no-deps -e TTCP_POSTGRES_PASSWORD= "$service"; then
        echo "FAIL: $service accepted empty configuration" >&2; exit 1
    fi
done
dc stop api worker
for service in api worker; do
    exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$(dc ps -aq "$service")")
    # Uvicorn may re-raise SIGTERM after completing lifespan shutdown (128 + 15).
    if [ "$exit_code" != 0 ] && ! { [ "$service" = api ] && [ "$exit_code" = 143 ]; }; then
        echo "FAIL: $service stopped with exit code $exit_code" >&2; exit 1
    fi
    dc logs "$service" | grep -q "${service}_stopped"
done
dc ps
echo 'PASS: bootstrap, jobs, Redis loss recovery, shutdown, auth, PostgreSQL persistence, monitoring'
