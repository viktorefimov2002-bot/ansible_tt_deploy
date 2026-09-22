# Execution adapter — TTCP-008

TTCP-009 extends this foundation with server resolution, encrypted management keys
and a separate structured pre-flight callback. See [server management](servers.md)
and [SSH trust decision](adr/0014-server-ssh-trust.md). The historical TTCP-008 scope
and validation record below describe the original foundation.

`apps/execution/ports.py` defines ExecutionPort, closed Pydantic requests, structured
results and safe events. `ansible.py` implements it; `jobs.py` supplies TTCP-007
handlers without importing the Ansible implementation. See [ADR-0004](adr/0004-ansible-execution-adapter.md).

## Supported foundation

| Operation | Target | Existing asset | Policy |
| --- | --- | --- | --- |
| `execution.validate` | None | `status.yml --syntax-check` | Offline, replay/cancel safe |
| `server.status` | Exactly one structured SSH target | `status.yml` | Read-only, replay/cancel safe |

`StatusParameters` allows only bounded log-line count and certificate warning days.
No generic execution endpoint is exposed. There is no server creation, deployment,
bootstrap or pre-flight business workflow. A successful exit means the playbook
ran successfully, **not** that the VPN service is healthy: the existing status
playbook reports unhealthy service conditions without necessarily failing Ansible.
Health/result extraction is a later explicitly typed contract.

The default worker registers `internal.execution.validate` alongside `internal.noop`.
An internal authorized caller can create it with `JobService.create`:
`kind="internal.execution.validate", target_type="internal", target_id=None,
replay_safe=True, cancellable=True` plus the existing actor, correlation and
idempotency fields. It goes through durable claiming, bounded logs and normal
completion; no special API is added.

For future status workflows, supply `RequestResolver.resolve(target_id)` to
`execution_handlers(port, resolver)` at worker composition. The resolver loads the
authorized target and ephemeral credentials from the future business/secret layer;
it must return a `server.status` request for that exact target. Create a job with
`kind="server.status", target_type="server", target_id=<UUID>, replay_safe=True,
cancellable=True`. Do not serialize ExecutionRequest into PostgreSQL job fields,
audit metadata or Redis. No resolver or SSH credential persistence is invented in
TTCP-008; the shipped default worker therefore does not register remote status jobs.

## Input, credentials and logs

Only hostname/IP, SSH user/port, a pinned public host key, exactly one password or
private key, and typed parameters are accepted. Unknown fields and template markers
in credentials are rejected (Ansible inventory values can otherwise evaluate
Jinja). Pass private key **content**, never a caller-selected path. Encrypted keys,
SSH agent forwarding and become-password support are not part of this contract;
status requires root or a management user with appropriate passwordless sudo.

Each execution gets a mode-0700 directory and mode-0600 files. Passwords are in
the ephemeral inventory, private keys in a separate file, never argv/environment.
The child gets no application secrets, SSH agent, inherited Ansible configuration
or plugin paths. It uses strict host-key checking and ignores SSH config files.
Temporary material is deleted after cleanup, including failure and cancellation.
An abrupt SIGKILL can leave temporary files until container recreation; use tmpfs
and do not share the worker UID with untrusted processes.

The callback emits only fixed task outcome codes. Stdout is parsed incrementally;
stderr/unknown text produces at most one suppression event. Raw output is never
persisted or forwarded. At most 150 output events per attempt reach PostgreSQL and
Redis, retaining TTCP-007's 200-entry history and SSE repair behavior.

Exit 0 succeeds; exit 4 is unreachable; all other process exits fail. Spawn/runtime
errors, invalid inputs and deadlines have distinct safe error codes. Numeric exit
status is returned internally, but job metadata retains the safe category only.
Ansible failures are not automatically retried. TTCP-007 timeout/shutdown/recovery
can replay these read-only handlers within their existing attempt budget.

## Timeouts and cancellation

Adapter deadline is 1–45 seconds (default 45), below worker's 60-second deadline
and 90-second lease. Poll durable cancellation/claim ownership every 250 ms even
when the child is silent. On cancellation, timeout, lost ownership or output sink
failure, stop the local process group with TERM, then KILL after 200 ms, and reap
the leader before releasing temporary files and acknowledging completion.
This bounds local cleanup, not remote rollback. Hard process/host loss is handled
by TTCP-007 lease recovery. Mutation handlers must not copy the read-only policy.

## Runtime and checks

Compose builds worker target `worker` in `apps/Dockerfile` and provides a 64 MiB
`/tmp` tmpfs. API/migration images remain on the original runtime. The worker keeps
its existing network restrictions; enabling remote server routing belongs to the
server-management rollout. The image contains only the allowlisted status asset
and checked-in defaults, not operator inventory, vars or local credentials.

On a Linux development controller, install `requirements-execution.lock` in the
same environment as the Control Plane. OpenSSH and sshpass must be on the standard
system PATH. The adapter's executable setting is trusted composition configuration,
never an API input; an absolute executable path supports a virtual environment.
Ansible's installed `/usr/local/bin` entry point is also supported in the container.

```sh
uv pip compile pyproject.toml --python-version 3.12 --universal --extra execution --constraint requirements-control-plane.lock -o requirements-execution.lock
python -m pytest -q
python -m ruff check apps tests migrations
python -m ruff format --check apps tests migrations
python -m compileall -q apps migrations tests
bash automation/ansible/tests/layout_smoke.sh
```

`tests/test_execution.py::test_real_ansible_adapter_smoke` runs the actual adapter
and real Ansible against the existing playbook using syntax-check only: no SSH,
remote infrastructure or mutations. POSIX tests additionally exercise real child
and descendant termination, both pipes and timeouts. Those checks skip explicitly
on Windows/missing Ansible. PostgreSQL job integration uses the existing
`TTCP_TEST_POSTGRES=1` isolated-schema fixture; Redis checks retain TTCP-007's flags.

Existing CLI checks and entry points are unchanged. Deploy/update/uninstall and
credential operations remain outside the adapter allowlist until their schemas,
controller-file dependencies and uncertain remote outcome handling are implemented.

## Validation record (2026-09-21, Windows controller)

PostgreSQL checks used a new disposable local cluster on loopback port 55438 and
the existing per-test isolated-schema fixture (`TTCP_TEST_POSTGRES=1`). Test
credentials were supplied through environment variables, never command arguments.
The cluster was stopped after validation.

| Command | Result |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 134 passed, 4 skipped |
| `.venv/Scripts/python.exe -m pytest tests/test_execution.py -q` | Final adapter changes: 39 passed, 3 skipped |
| `.venv/Scripts/python.exe -m pytest tests/test_execution_jobs.py -q` | Final job integration: 10 passed |
| `.venv/Scripts/python.exe -m ruff check apps tests migrations` | Passed |
| `.venv/Scripts/python.exe -m ruff format --check apps tests migrations` | Passed, 46 files |
| `.venv/Scripts/python.exe -m compileall -q apps migrations tests` | Passed |
| `.tools/uv.exe tool run --cache-dir .tools/uv-cache --from mypy mypy --python-executable .venv/Scripts/python.exe --platform linux --follow-imports=silent --ignore-missing-imports --check-untyped-defs apps/execution apps/jobs/worker.py apps/jobs/service.py` | Passed, 8 files; Linux is the adapter runtime platform |
| `.tools/docker-compose.exe -f infra/compose/compose.yaml config --quiet` | Passed with disposable validation environment values |
| `bash automation/ansible/tests/layout_smoke.sh` | Passed, including init; Git Bash with local Python/PyYAML shim |
| `git -c core.safecrlf=false diff --check` | Passed; final tracked diff and new files reviewed |
| `bash infra/compose/tests/runtime_smoke.sh` | Unavailable: Docker command/engine absent |

The four suite skips are two real POSIX process-tree tests, the real Ansible syntax
smoke, and the real Redis roundtrip/restart-loss integration test. These require a
Linux/Ansible controller and a Redis service respectively. Fake-OS cleanup tests
cover timeout, repeated cancellation, spawn cancellation and output-sink failure;
PostgreSQL integration covers worker lifecycle and simulated Redis unavailability.
No external server was contacted. Container build and live SSH authentication have
not been verified in this environment.
