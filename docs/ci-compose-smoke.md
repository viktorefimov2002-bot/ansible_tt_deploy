# TTCP-021 Compose smoke recovery

## Incident and verified cause

[Run 37908616392](https://github.com/viktorefimov2002-bot/ansible_tt_deploy/actions/runs/37908616392)
tested `6f1d6d9e41d285cf63e713c266a8eb8a6849eeb4`. Both web jobs, E2E and
Python/integration (including real MinIO backup/restore) succeeded. Compose exited
255 after `Control Plane entrypoint`, before the Redis outage stage.
[Run 37898407431](https://github.com/viktorefimov2002-bot/ansible_tt_deploy/actions/runs/37898407431)
at `e402dd2b99227186b1faa2b1a69c560824702ea1` passed Compose. The runtime smoke
script and base Compose manifest are identical between those commits. Both logs
show successful PostgreSQL pool recovery: two ready responses, zero unavailable
responses, approximately 240вЂ“250 ms. This incident does not establish a reconnect
defect or a need for a longer readiness deadline.

The offending command was reproduced separately with the exact NGINX runtime
image, repository routing/security configuration and compiled applications:

```sh
dc exec -T nginx sh -c 'wget --header="Host: vpn.localhost" -S -O /dev/null http://127.0.0.1:8080/ 2>&1' |
  grep -qi 'Cache-Control: no-store'
```

`grep -q` exits immediately on the header match. Wget can still emit headers or
progress output through Compose's stream. Closing that pipe makes **Compose exec
exit 255 while grep exits 0**; `pipefail` and `errexit` then abort the smoke.
The [GNU grep manual](https://www.gnu.org/software/grep/manual/grep.html)
documents early input closure causing an upstream failure and a Bash exit.
The race depends on stream timing, explaining why an unchanged script passed
earlier. Historical logs cannot identify a line retrospectively, but the exact
header pipeline reproduced this same exit pair in 24 of 30 trials. Client/admin
HTML and default-host denial pipelines passed all 30 original trials.

## Fix and diagnostics

`infra/compose/tests/smoke_assertions.sh` captures and drains the complete command
output before checking its exit status and response. A matching response cannot
hide a failed producer. The smoke uses fixed checkpoints for builds, migrations,
jobs, authentication, metrics, collector restart, every edge assertion, each
dependency outage/recovery, configuration rejection and shutdown.

Diagnostics contain checkpoint labels, assertion names, source line numbers and
numeric exit codes only. Captured responses, service logs, commands, environment,
URLs and secret values are never echoed. There is no `set -x` or diagnostic artifact
upload. Case-sensitive body/metric assertions remain case-sensitive; only the
existing cache-header check ignores case.

Expected HTTP denials require both wget exit 1 and the existing 404/503 response
match. Worker probes/startup require exit 1; API dependency startup requires
Uvicorn's exit 3; missing configuration requires exit 1. A Docker/Compose failure
cannot satisfy these negative checks. Metric polls retry wget request failures or
a missing metric within their existing bounds; unexpected exit codes such as 255
fail immediately. Two consecutive API ready responses, liveness, unchanged API
identity, SQL persistence, durable jobs and graceful shutdown remain mandatory.
No service, routing, port, network, firewall, backup or product implementation was
changed. No timeout or recovery sleep was increased.

The Compose job now runs the entire smoke three times, each with fresh generated
credentials, a unique project and fresh volumes. A failure aborts the job. There
are no optional runs or skipped assertions.

## Verification and acceptance

On the implementation host (Windows with Linux Docker 24.0.2/Compose 2.18.1):

- An isolated, network-disabled Compose producer reproduced exit 255/grep 0 in
  20/20 early-close trials; draining the stream passed 20/20. The actual new capture
  helper passed 20/20 against the same delayed large-output producer.
- The exact NGINX edge fixture described above reproduced the original cache
  pipeline failure 24/30 times. All five fixed checks (entrypoint health, both
  applications, cache policy and default-host denial) passed 30/30 sequences.
  These checks used a fresh network-disabled project without published ports;
  they do not certify full-stack recovery or Docker 28 publication/firewall gates.
- Linux Bash syntax, repository Ruff lint/format, mypy and all five Compose overlay
  validations passed. Regression tests exercise actual Bash pipe closure, response
  mismatches, producer failure, expected denial exits, case policy, bounded HTTP
  recovery and a secret canary. Exact final commands/results are recorded in the
  completion report.
- Full runtime smoke was attempted locally but blocked while pulling PostgreSQL/
  Redis: the Linux daemon could not resolve/reach Docker Hub. The pinned NGINX image
  was downloaded through Windows with every blob SHA-256 verified, then loaded
  into Linux for the isolated reproduction. Linux DNS/daemon settings were not
  modified. Docker 24/Compose 2.18 also cannot certify the documented full release
  environment (Docker 28+/Compose 2.24.4+).
- A local Linux MinIO image drill reached real TLS readiness and real mc bucket
  creation, then failed with `ConnectTimeout` in its Windows-to-WSL stdio relay;
  the backup job correctly remained queued with `backup_unknown`. This local
  workaround is not the Linux CI transport. Its failed attempts are not passes.
- The unchanged full backup/verification/restore test passed separately with
  disposable native PostgreSQL 17 and real MinIO/mc binaries at the exact same
  release commits as the CI source images: **1 passed, zero skips**, 18.24 seconds.
  One pytest cache-permission warning did not affect test execution. This confirms
  the real storage/encryption/restore flow locally, while Linux image transport
  acceptance still requires the next CI run.

The final affected regression command was:

```powershell
.venv/Scripts/python.exe -m pytest tests/test_compose_smoke_assertions.py tests/test_ci_recovery.py tests/test_ci_safety.py tests/test_docker_context.py tests/test_ci_backup_networking.py tests/test_entrypoints.py -q -p no:cacheprovider --basetemp=.tools/pytest-smoke021-final
```

Result: **45 passed, zero skips**, 21.43 seconds. Additional exact local commands:

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations scripts/ci` | Passed. |
| `.venv/Scripts/python.exe -m ruff format --check apps tests migrations scripts/ci` | 133 files passed. |
| `.venv/Scripts/python.exe -m mypy` | 65 source files passed. |
| `.venv/Scripts/python.exe scripts/ci/compose_validation.py --compose-executable .tools/docker-compose.exe` | All five overlay combinations passed. |
| Linux `bash -n infra/compose/tests/runtime_smoke.sh infra/compose/tests/smoke_assertions.sh` | Passed. |
| Linux `bash .tools/repro021-pipe.sh` | Original live pipe failed 20/20; draining passed 20/20. Its final unavailable optional `httpd` inventory probe exited 1; the measured pipe trials completed. |
| Linux `bash .tools/repro021-capture.sh` | Actual capture helper passed 20/20, exit 0. |
| Linux `bash .tools/repro021-nginx.sh` | Original exact cache assertion failed 24/30; fixed edge sequences passed 30/30, exit 0. |
| `.venv/Scripts/python.exe .tools/run-backup021-local.py` | Real native MinIO backup/restore: 1 passed, zero skips, 18.24 seconds; one cache warning. |
| Linux `bash infra/compose/tests/runtime_smoke.sh` | Two full-stack attempts blocked at registry pulls; final diagnostic: `stage=database-broker-start`, exit 18. |

The `.tools` reproduction scripts/binaries are ignored local validation artifacts,
not release inputs or substitutes for mandatory CI. They were run through Ubuntu
WSL; Linux checks used `C:/Windows/System32/wsl.exe -d Ubuntu --cd
/mnt/c/Users/v1kt0/Documents/ansible_tt_deploy --exec bash ...`. All actual CI
regression tests are tracked and run by the unchanged mandatory Python runner.

After the owner commits and pushes the reviewed change, require **one complete
Release validation run at that exact SHA** with all five results successful:
Python/integration, web/client-web, web/admin-web, Compose and E2E. Do not combine
results from different commits or treat the four green jobs at `6f1d6d9e` as
validation of the new change. Compose must show all three `compose-smoke-run`
checkpoints and final PASS messages; Python must show zero skipped tests and the
real MinIO backup/verification/restore pass. Keep the existing mandatory network
drill and all other gates enabled.

On a supported disposable Linux host, reproduce with:

```sh
python scripts/ci/compose_validation.py
for attempt in 1 2 3; do
  printf 'CHECK: compose-smoke-run=%s\n' "$attempt"
  bash infra/compose/tests/runtime_smoke.sh || exit "$?"
done
```

Use the complete command list in [release readiness](release-readiness.md) for
the other required gates, including `python scripts/ci/python_validation.py` and
its mandatory real backup drill. Release acceptance remains pending until those
same-SHA CI results and the separate staging/live acceptance record exist.
