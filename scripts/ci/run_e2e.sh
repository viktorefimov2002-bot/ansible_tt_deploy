#!/usr/bin/env bash
# Linux only. Every runtime dependency is disposable and has no external egress.
set -euo pipefail

# Do not allow a developer's deployment settings or remote Docker context to
# override the generated fixture. No environment values are printed.
while IFS= read -r name; do
  case "$name" in
    TTCP_*|CI_POSTGRES_PASSWORD|CI_REDIS_PASSWORD|CI_AUTH_KEY|CI_E2E_*|DOCKER_*|COMPOSE_*)
      unset "$name" ;;
  esac
done < <(compgen -e)

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo"
mkdir -p .tools
state=$(mktemp -d "$repo/.tools/ci-e2e-XXXXXXXX")
project="ttcp-ci-e2e-$(basename "$state" | tr '[:upper:]' '[:lower:]')"
export TTCP_E2E_ENV_FILE="$state/environment"
export TTCP_E2E_PROJECT="$project"
export TTCP_E2E_FIXTURE="$state/fixture/browser.json"
export TTCP_E2E_HTTPS_PORT=18443
export TTCP_CI_E2E_NETWORK="$project-ingress"

# An explicit local socket ignores an operator's remote Docker context/DOCKER_HOST.
compose=(docker --host unix:///var/run/docker.sock compose --project-name "$project"
  --env-file "$TTCP_E2E_ENV_FILE" -f "$repo/infra/compose/compose.ci-e2e.yaml")

cleanup() {
  result=$?
  trap - EXIT
  # Keep output free of seeded tokens and configuration bodies. No trace artifacts.
  if (( result != 0 )); then "${compose[@]}" ps || true; fi
  if ! "${compose[@]}" down --volumes --remove-orphans >/dev/null 2>&1; then result=1; fi
  if [[ -f "$state/network.json" ]]; then
    if ! python scripts/ci/networking.py cleanup --state "$state/network.json"; then
      printf '%s\n' 'Guarded CI network cleanup failed; disposable state retained for recovery' >&2
      exit 1
    fi
  fi
  # Only remove the verified, task-owned mktemp directory under this checkout.
  case "$state" in "$repo"/.tools/ci-e2e-*) rm -rf -- "$state" ;; esac
  exit "$result"
}
trap cleanup EXIT

python - "$state" <<'PY'
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

state = Path(sys.argv[1])
fixture = state / "fixture"
fixture.mkdir(mode=0o700)
tls = state / "tls"
for role, host in (("admin", "admin.localhost"), ("client", "vpn.localhost")):
    directory = tls / role
    directory.mkdir(parents=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (directory / "fullchain.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = directory / "privkey.pem"
    private.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    private.chmod(0o600)
environment = state / "environment"
environment.write_text("\n".join([
    "CI_POSTGRES_PASSWORD=" + secrets.token_hex(24),
    "CI_REDIS_PASSWORD=" + secrets.token_hex(24),
    "CI_AUTH_KEY=" + Fernet.generate_key().decode(),
    "CI_E2E_FIXTURE_DIR=" + str(fixture),
    "CI_E2E_TLS_DIR=" + str(tls),
    "CI_E2E_HTTPS_PORT=18443",
]) + "\n")
environment.chmod(0o600)
PY

"${compose[@]}" config --quiet
"${compose[@]}" build api nginx
python scripts/ci/networking.py create --name "$TTCP_CI_E2E_NETWORK" --state "$state/network.json"
"${compose[@]}" up -d --wait --wait-timeout 120 postgres redis
"${compose[@]}" run --rm migrate
"${compose[@]}" run --rm --user "$(id -u):$(id -g)" seed
"${compose[@]}" up -d --wait --wait-timeout 120 api worker nginx
"${compose[@]}" exec -T nginx nginx -t
python scripts/ci/e2e_probe.py --port "$TTCP_E2E_HTTPS_PORT" --tls-dir "$state/tls"
# Playwright failure contexts may contain DOM/configuration data; destroy those
# with the task-owned fixture directory as well, and never upload them.
pnpm --dir e2e test:diagnostics
pnpm --dir e2e test --output "$state/browser-results"
