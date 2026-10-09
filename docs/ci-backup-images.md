# TTCP-021 backup fixture image availability

[Actions run 37898407431](https://github.com/viktorefimov2002-bot/ansible_tt_deploy/actions/runs/37898407431)
tested `e402dd2b99227186b1faa2b1a69c560824702ea1`. Both Web results, Compose and
E2E passed. Python completed 547 tests with zero skips, then the mandatory backup
drill failed pulling `minio/minio`; MinIO had not started. The subsequent Ansible
layout step was skipped because of that failure, so the run does not meet release
acceptance.

## Registry verification and decision (2026-10-09)

A direct Quay replacement was investigated before changing the fixture. MinIO's
[release README](https://github.com/minio/minio/blob/RELEASE.2025-04-22T22-12-26Z/README.md)
documents `quay.io/minio/minio`; the official
[mc release publication script](https://github.com/minio/mc/blob/RELEASE.2025-04-16T18-13-26Z/docker-buildx.sh)
publishes `quay.io/minio/mc`. Both repositories denied anonymous pull from this
implementation host; GitHub runner access could not be certified:

- Local Linux Docker `manifest inspect --verbose` failed for both existing release
  tags on Quay. Registry requests using fresh anonymous tokens returned HTTP 401;
  token access rules contained no `pull` action. Tokens were never logged.
- Later upstream-referenced MinIO/mc tags also failed. The same anonymous mechanism
  granted `pull` for public `prometheus/prometheus`, distinguishing this from a
  blanket Quay/network failure.
- Official Linux archive/checksum downloads returned HTTP 410 with an archive
  notice. A registry or binary-download fallback would therefore remain unverified.
  MinIO's [current source-only distribution instructions](https://github.com/minio/minio#source-only-distribution)
  provide the viable upstream-source route.

The CI-only Compose file now builds two local images from the **same existing
official release sources**, rather than substituting an unverified registry or
third-party mirror. This is an explicit deviation from the preferred Quay pull
approach. No product backup logic or deployment image was changed.

| Component | Official release | Exact commit | Source archive SHA-256 |
| --- | --- | --- | --- |
| MinIO | `RELEASE.2025-04-22T22-12-26Z` | `0d7408fc9969caf07de6a8c3a84f9fbb10a6739e` | `7eb30a913fea30f18069abf194e1e78e4983b558cc526911ae1c11396a9859a5` |
| mc | `RELEASE.2025-04-16T18-13-26Z` | `b00526b153a31b36767991a4f5ce2cced435ee8e` | `4cd13e34daeeb8481c3ba8686b082f161b8dc1f7aad52d715a706a587349c6ae` |

The annotated upstream tags were resolved through GitHub's API, and the archive
hashes were verified against the actual downloaded bytes. The Dockerfile verifies
each checksum before unpacking. Upstream `go.mod`/`go.sum` govern dependencies,
Go's checksum verification remains enabled, and automatic toolchain upgrades are
disabled. Release/commit metadata is fixed at build time.

The compiler is `golang:1.24.2-alpine3.21` at index digest
`sha256:7772cb5322baa875edd74705556d08f0eeca7b9c4b5367754ce3f2f00041ccee`.
The runtime is `curlimages/curl:8.12.1` at index digest
`sha256:94e9e444bcba979c2ea12e27ae39bee4cd10bc7041a472c4727a558e213744e6`.
Both manifest byte hashes matched their registry content-digest headers, and both
images were actually pulled using local Linux Docker without private credentials.
The runtime provides the existing curl/CA health probe and shell-based mc setup.

## Mandatory behavior

`backup_validation.py` runs `compose build --pull minio setup` before allocating
its guarded network. Both images must build successfully. Runtime startup/setup
use `up --no-build --pull never` and `run --no-deps --pull never`: they cannot
silently switch back to registry images or recreate MinIO during bucket setup.
The build context includes only its Dockerfile and ignore policy; it excludes
fixtures, credentials and the repository's operator configuration.

The existing loopback-only publication, owned firewall rules, TLS CA checks, S3
SigV4 verification, conditional writes, streaming authenticated encryption, complete
remote readback and empty-target restore test remain mandatory. There is no skip,
mock storage, TLS bypass or backup-logic fallback. Builds may download only their
specified source/dependency inputs; runtime egress remains blocked by the original
guarded bridge. These archived release builds are disposable test infrastructure,
not a recommendation for production object storage.

## Acceptance after the next reviewed push

No commit, push or workflow dispatch is performed by this change. A rerun of the
old run tests the old SHA. After the owner reviews and pushes a new commit:

1. Require `python-integration`, `web (client-web)`, `web (admin-web)`, `compose`
   and `e2e` to succeed on the same exact new SHA. Do not combine prior green jobs.
2. In Python logs require both pinned bases pulled/checked, both image targets
   built, published MinIO HTTPS readiness, bucket setup, and
   `tests/integration/test_backup_release.py` passed with zero skips. The main
   Python suite and Ansible layout gate must also finish without skipped checks.
3. Keep the full real backup → signed verification/readback → restore drill; a
   successful build or `--version` check alone is insufficient.
4. Use the exact Linux commands in [release readiness](release-readiness.md) and
   [network verification](ci-networking.md). Docker 28+, the scoped firewall and
   disposable-database guard remain required. Never upload fixture secrets,
   registry tokens, environment files, plaintext dumps/configurations or TLS keys.

GitHub-hosted pull/build availability and five-job acceptance on the new SHA remain
pending that run; local Linux pulls/builds cannot certify GitHub runner behavior.

## Local verification evidence

| Executed check | Result |
| --- | --- |
| Linux `docker pull golang:1.24.2-alpine3.21@sha256:7772cb5322baa875edd74705556d08f0eeca7b9c4b5367754ce3f2f00041ccee` and `docker pull curlimages/curl:8.12.1@sha256:94e9e444bcba979c2ea12e27ae39bee4cd10bc7041a472c4727a558e213744e6` | Both pulled anonymously and matched their pinned index digests. |
| From `infra/compose/ci-backup`: Linux `docker build --pull --target mc -t ttcp-ci-mc:RELEASE.2025-04-16T18-13-26Z .` and `docker build --pull --target minio -t ttcp-ci-minio:RELEASE.2025-04-22T22-12-26Z .` | Both passed archive checksums, locked module verification and compilation. Images built successfully. |
| Linux `docker run --rm --network none <each-built-image> --version` | Exact release tags and commits above; Go 1.24.2, Linux/amd64. |
| `.venv/Scripts/python.exe .tools/backup021-container-local.py` | **1 passed, zero skips** in 33.21 seconds: real Linux MinIO/mc images, real PostgreSQL 17, trusted TLS, signed storage, encryption, verification/readback and maintenance restore. No object-store/HTTP/dump/restore behavior was mocked. Both disposable services stopped afterward. |
| `.venv/Scripts/python.exe -m pytest -q tests/test_ci_backup_networking.py tests/test_ci_networking.py tests/test_ci_safety.py --basetemp=.tools/pytest-ci-backup-images-20261009-cli --tb=short -p no:cacheprovider` | **65 passed, zero skips**; includes source/build-context pinning, build failure, readiness failure, ownership cleanup and real sockets/TLS. |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations scripts/ci`; `.venv/Scripts/python.exe -m ruff format --check apps tests migrations scripts/ci`; `.venv/Scripts/python.exe -m mypy`; `.venv/Scripts/python.exe scripts/ci/compose_validation.py --compose-executable .tools/docker-compose.exe`; CI-backup `compose config --quiet` with generated values; `git diff --check` | Passed; five deployment overlays plus the modified standalone CI fixture parse correctly. |
| `.tools/docker-compose.exe run --rm --no-deps --pull never --help`; `up -d --wait --no-build --pull never --help`; `build --pull --help` | All action flags parsed successfully. `run` supports `--no-deps`, whereas `--no-build` is an `up` flag. |

The ignored local container-drill launcher was necessary because this host still
has Docker 24.0.2. It runs MinIO with `--network none`, mc in that same isolated
network namespace, and native disposable PostgreSQL. An IPv4-loopback-only host
listener forwards TLS bytes unchanged over `docker exec` stdio to the owned MinIO
container. There is no Docker-published port, certificate bypass, protocol mock or
connection to a managed node. This verifies the **new Linux images with the full
real integration test**, while deliberately avoiding the pre-28 Docker publication
defect. It does not certify the Linux CI firewall/Compose harness: that remains
mandatory on Docker 28+ in the next exact-SHA run. No safeguard or release check
was disabled in the tracked CI runner.
