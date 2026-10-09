#!/bin/bash
# Integration test: isolated, disposable Docker Compose project, never ttcp-dev.
set -Eeuo pipefail
COMPOSE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
source "$COMPOSE_DIR/tests/smoke_assertions.sh"
checkpoint docker-preflight
docker compose version >/dev/null
docker info >/dev/null
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
checkpoint empty-secrets-denial
if dc config --quiet >/dev/null 2>&1; then
    fail empty-secrets-accepted
fi
checkpoint synthetic-secrets
for name in POSTGRES_PASSWORD REDIS_PASSWORD GRAFANA_ADMIN_PASSWORD; do
    value=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
    printf '%s=%s\n' "$name" "$value" >> "$TEST_DIR/runtime.env"
done
unset value
printf 'HTTP_PORT=0\nGRAFANA_PORT=0\n' >> "$TEST_DIR/runtime.env"
printf 'TTCP_AUTH_ENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32)" >> "$TEST_DIR/runtime.env"
checkpoint compose-config
dc config --quiet
checkpoint database-broker-start
dc up -d --wait --wait-timeout 180 postgres redis
checkpoint application-build
dc build api worker migrate metrics-collector
# Explicit deployment step, never performed automatically by API/worker startup.
checkpoint migration-upgrade
dc run --rm --no-deps migrate
checkpoint migration-idempotency
dc run --rm --no-deps migrate
checkpoint migration-drift
dc run --rm --no-deps migrate python -m alembic check
checkpoint stack-start
dc up -d --build --wait --wait-timeout 180
checkpoint nginx-config
dc exec -T nginx nginx -t
checkpoint api-initial-health
dc exec -T api python -m apps.shared.healthcheck api
checkpoint worker-initial-health
dc exec -T worker python -m apps.shared.healthcheck worker
sql() { dc exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -At' ; }
api_identity() {
    docker inspect --format '{{.Id}} {{.State.StartedAt}} {{.RestartCount}}' "$(dc ps -aq api)"
}
wait_api_recovery() {
    # Probe the live API's existing pools. Dependency health or a new worker
    # connection cannot demonstrate recovery of those pools.
    dc exec -T api python - < "$COMPOSE_DIR/../../scripts/ci/api_recovery.py"
    expect_exit "$stage-identity" 0 api_identity
    if [ "$output" != "$api_before" ]; then
        fail api-restarted-instead-of-reconnecting
    fi
    unset output
}
# Use only this disposable project's database/Redis. No public job creation API.
create_job() {
    printf "INSERT INTO jobs(type,target_type,request_id,idempotency_key,replay_safe,cancellable) VALUES ('internal.noop','internal','compose-smoke',gen_random_uuid()::text,true,true) RETURNING id;\n" | sql | sed -n '1p'
}
wait_job() {
    for attempt in $(seq 1 30); do
        state=$(printf "SELECT status FROM jobs WHERE id='%s';\n" "$1" | sql)
        if [ "$state" = succeeded ]; then return; fi
        if [ "$state" = failed ]; then fail internal-job-failed; fi
        sleep 1
    done
    fail durable-job-deadline
}
checkpoint initial-durable-job
job_id=$(create_job)
wait_job "$job_id"
checkpoint worker-stop
dc stop worker
checkpoint queued-durable-job
job_id=$(create_job)
checkpoint redis-queue-hint
dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli LPUSH ttcp:jobs:queue "$1"' sh "$job_id" >/dev/null
checkpoint redis-recreate
dc up -d --force-recreate --no-deps --wait --wait-timeout 120 redis
checkpoint worker-restart
dc up -d --wait --wait-timeout 120 worker
checkpoint redis-loss-job-recovery
wait_job "$job_id"
job_attempts() { printf "SELECT attempts FROM jobs WHERE id='%s';\n" "$1" | sql; }
assert_output recovered-job-single-attempt '^1$' 0 job_attempts "$job_id"
assert_output recovered-job-events '^[1-9][0-9]*$' 0 dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli XLEN "ttcp:jobs:log:$1"' sh "$job_id"
checkpoint postgres-persistence-write
printf 'CREATE TABLE runtime_probe (value text); INSERT INTO runtime_probe VALUES ($$persistent$$);\n' | sql
expect_exit postgres-recreate-identity 0 api_identity
api_before=$output
unset output
checkpoint postgres-recreate
dc up -d --force-recreate --no-deps --wait --wait-timeout 120 postgres
checkpoint postgres-api-recovery
wait_api_recovery
persistence_read() { printf 'SELECT value FROM runtime_probe;\n' | sql; }
assert_output postgres-persistence-read '^persistent$' 0 persistence_read
checkpoint postgres-job-recovery
job_id=$(create_job)
wait_job "$job_id"
assert_output postgres-password-denial 'password authentication failed' 2 dc exec -T postgres sh -c 'PGPASSWORD=incorrect psql -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1"'
assert_output redis-authenticated-ping '^PONG$' 0 dc exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
assert_output redis-noauth-denial NOAUTH 0 dc exec -T redis redis-cli ping
assert_output redis-password-denial WRONGPASS 0 dc exec -T redis sh -c 'REDISCLI_AUTH=incorrect redis-cli ping'
assert_output metrics-health '^OK$' 0 dc exec -T victoriametrics wget -qO- http://127.0.0.1:8428/health
assert_output grafana-metrics-health '^OK$' 0 dc exec -T grafana wget -qO- http://victoriametrics:8428/health
assert_output grafana-database-health '"database"[[:space:]]*:[[:space:]]*"ok"' 0 dc exec -T grafana wget -qO- http://127.0.0.1:3000/api/health
checkpoint collector-health
dc exec -T metrics-collector python -m apps.shared.healthcheck monitoring
# The collector also retains SQL connections across PostgreSQL replacement.
# Exercise that existing process until its actual metrics query recovers.
metrics_deadline=$((SECONDS + 45))
for metric in ttcp_node_scrape_success ttcp_service_active; do
    checkpoint "collector-recovery-$metric"
    until probe_output "# TYPE $metric gauge" dc exec -T victoriametrics wget -T 5 -qO- http://metrics-collector:9101/metrics; do
        if (( SECONDS >= metrics_deadline )); then fail collector-recovery-deadline; fi
        sleep 0.2
    done
done
checkpoint metrics-scrape
for attempt in $(seq 1 12); do
    if probe_output 'ttcp-managed-nodes' dc exec -T victoriametrics wget -qO- 'http://127.0.0.1:8428/api/v1/query?query=up%7Bjob%3D%22ttcp-managed-nodes%22%7D'; then
        break
    fi
    [ "$attempt" -lt 12 ] || fail metrics-scrape-deadline
    sleep 5
done
checkpoint collector-stop
dc stop metrics-collector
assert_output api-live-without-collector '"ok"' 0 dc exec -T nginx wget -qO- http://api:8080/healthz
checkpoint collector-restart
dc up -d --wait --wait-timeout 120 metrics-collector
assert_output nginx-health '^Control Plane entrypoint$' 0 dc exec -T nginx wget -qO- http://127.0.0.1:8080/healthz
assert_output nginx-api-health '"ok"' 0 dc exec -T nginx wget -qO- http://api:8080/api/healthz
assert_output nginx-api-ready '"ready"' 0 dc exec -T nginx wget -qO- http://api:8080/api/readyz
assert_output client-root '<div id="root"' 0 dc exec -T nginx wget --header='Host: vpn.localhost' -qO- http://127.0.0.1:8080/
assert_output admin-root '<div id="root"' 0 dc exec -T nginx wget --header='Host: admin.localhost' -qO- http://127.0.0.1:8080/
assert_output_ci client-cache-policy 'Cache-Control: no-store' 0 dc exec -T nginx wget --header='Host: vpn.localhost' -S -O /dev/null http://127.0.0.1:8080/
assert_output default-host-denial '404 Not Found' 1 dc exec -T nginx wget -S -O /dev/null http://127.0.0.1:8080/api/example
# Runtime failures must not turn liveness into a restart loop; readiness recovers.
for dependency in redis postgres; do
    expect_exit "$dependency-outage-identity" 0 api_identity
    api_before=$output
    unset output
    checkpoint "$dependency-outage-stop"
    dc stop "$dependency"
    assert_output "$dependency-outage-liveness" '"ok"' 0 dc exec -T nginx wget -qO- http://api:8080/healthz
    assert_output "$dependency-outage-readiness" '503 Service' 1 dc exec -T nginx wget -S -O /dev/null http://api:8080/readyz
    expect_exit "$dependency-outage-worker-probe" 1 dc exec -T worker python -m apps.shared.healthcheck worker
    expect_exit "$dependency-outage-worker-start" 1 dc run --rm --no-deps worker
    expect_exit "$dependency-outage-api-start" 3 dc run --rm --no-deps api
    unset output
    checkpoint "$dependency-restart"
    dc up -d --wait --wait-timeout 120 "$dependency"
    checkpoint "$dependency-api-recovery"
    wait_api_recovery
    checkpoint "$dependency-services-recovery"
    dc up -d --wait --wait-timeout 120 api worker
    expect_exit "$dependency-services-identity" 0 api_identity
    test "$output" = "$api_before" || fail api-restarted-instead-of-reconnecting
    unset output
    assert_output "$dependency-recovered-ready" '"ready"' 0 dc exec -T nginx wget -qO- http://api:8080/readyz
done
for service in api worker; do
    expect_exit "$service-empty-config-denial" 1 dc run --rm --no-deps -e TTCP_POSTGRES_PASSWORD= "$service"
    unset output
done
checkpoint graceful-stop
dc stop api worker
for service in api worker; do
    checkpoint "$service-shutdown-exit"
    exit_code=$(docker inspect --format '{{.State.ExitCode}}' "$(dc ps -aq "$service")")
    # Uvicorn may re-raise SIGTERM after completing lifespan shutdown (128 + 15).
    if [ "$exit_code" != 0 ] && ! { [ "$service" = api ] && [ "$exit_code" = 143 ]; }; then
        echo "FAIL: stage=$stage stopped_exit=$exit_code" >&2; exit 1
    fi
    assert_output "$service-shutdown-event" "${service}_stopped" 0 dc logs "$service"
done
checkpoint final-state
dc ps
echo 'PASS: bootstrap, jobs, Redis loss recovery, shutdown, auth, PostgreSQL persistence, monitoring'
