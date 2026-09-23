"""Fixed playbook mapping, isolated inventory/config, and fail-closed output capture."""

import json
import os
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from apps.execution.ports import (
    CHECK_STATES,
    CHECKS,
    EventSink,
    ExecutionEvent,
    ExecutionRequest,
    ExecutionResult,
    Outcome,
)
from apps.execution.preflight import dns_checks
from apps.execution.process import run_process

ROOT = Path(__file__).resolve().parents[2]


class AnsibleExecutionAdapter:
    def __init__(self, *, executable: str | None = None, runner=run_process):
        # Deployment configuration, never populated from execution input or an API.
        self.executable = executable or str(Path(sys.executable).with_name("ansible-playbook"))
        self.runner = runner

    async def execute(self, request: ExecutionRequest, emit: EventSink) -> ExecutionResult:
        try:
            # Revalidate even model_construct/model_copy and nested forged models.
            request = ExecutionRequest.model_validate(
                {
                    **request.model_dump(warnings=False),
                    "target": None
                    if request.target is None
                    else {
                        **request.target.model_dump(warnings=False),
                        "password": request.target.password,
                        "private_key": request.target.private_key,
                    },
                }
            )
        except (ValidationError, AttributeError, TypeError):
            return ExecutionResult(Outcome.INVALID)

        checks = None
        if request.operation == "server.preflight":
            checks = dict.fromkeys(CHECKS, "unknown")
            checks.update(await dns_checks(request.preflight))
            if not request.preflight.acme_http:
                checks["tcp_80"] = "skipped"
        if self.runner is run_process and os.name != "posix":
            # Do not materialize credentials on unsupported controllers.
            return ExecutionResult(Outcome.UNAVAILABLE, checks=checks)

        try:
            with tempfile.TemporaryDirectory(prefix="ttcp-execution-") as directory:
                cwd = Path(directory)
                os.chmod(cwd, 0o700)
                argv, env = self._prepare(request, cwd)
                pending = b""
                suppressed = False
                emitted = 0

                async def output(chunk: bytes, stderr: bool):
                    nonlocal pending, suppressed, emitted
                    if emitted >= 150:
                        return  # Keep output and DB/event volume bounded per attempt.
                    if stderr:
                        if not suppressed:
                            suppressed = True
                            emitted += 1
                            await emit(ExecutionEvent.OUTPUT_SUPPRESSED)
                        return
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if checks is not None and line.startswith(b"ttcp_checks:"):
                            try:
                                report = json.loads(line[12:])
                                if not isinstance(report, dict) or len(report) > len(CHECKS):
                                    raise ValueError
                                for key, value in report.items():
                                    if (
                                        key in CHECKS
                                        and not key.startswith("dns_")
                                        and value in CHECK_STATES
                                    ):
                                        checks[key] = value
                                continue
                            except (ValueError, TypeError, UnicodeError):
                                pass
                        try:
                            event = ExecutionEvent(line.decode("ascii"))
                        except (ValueError, UnicodeDecodeError):
                            if suppressed:
                                continue
                            suppressed = True
                            event = ExecutionEvent.OUTPUT_SUPPRESSED
                        if emitted < 150:
                            emitted += 1
                            await emit(event)
                    if len(pending) > 4096:
                        pending = b""  # Raw oversized output is never retained.
                        if not suppressed and emitted < 150:
                            suppressed = True
                            emitted += 1
                            await emit(ExecutionEvent.OUTPUT_SUPPRESSED)

                await emit(ExecutionEvent.STARTED)
                code = await self.runner(argv, cwd, env, output, request.timeout_seconds)
                if pending:
                    await output(b"\n", False)
                outcome = {0: Outcome.SUCCEEDED, 4: Outcome.UNREACHABLE}.get(code, Outcome.FAILED)
                if checks is not None and outcome == Outcome.UNREACHABLE:
                    checks["ssh"] = "fail"
                return ExecutionResult(outcome, code, checks)
        except TimeoutError:
            return ExecutionResult(Outcome.TIMED_OUT, checks=checks)
        except OSError:
            return ExecutionResult(Outcome.UNAVAILABLE, checks=checks)

    def _prepare(self, request: ExecutionRequest, cwd: Path):
        def write(name: str, content: str):
            path = cwd / name
            with path.open("x", encoding="utf-8") as file:
                os.chmod(path, 0o600)
                file.write(content)

        target = request.target
        host: dict[str, str | int] = {"ansible_connection": "local"}
        if target is not None:
            host = {
                "ansible_host": target.host,
                "ansible_user": target.user,
                "ansible_port": target.port,
                "ansible_connection": "ssh",
                "ansible_ssh_args": (
                    "-F /dev/null -o StrictHostKeyChecking=yes "
                    "-o UserKnownHostsFile=known_hosts -o GlobalKnownHostsFile=/dev/null "
                    "-o ControlMaster=no -o IdentitiesOnly=yes -o ConnectTimeout=10"
                ),
            }
            name = target.host if target.port == 22 else f"[{target.host}]:{target.port}"
            write("known_hosts", f"{name} {target.host_key}\n")
            if target.password is not None:
                host["ansible_password"] = target.password.get_secret_value()
                host["ansible_ssh_common_args"] = "-o PubkeyAuthentication=no"
            else:
                assert target.private_key is not None
                write("identity", target.private_key.get_secret_value())
                host["ansible_ssh_private_key_file"] = str(cwd / "identity")
                host["ansible_ssh_common_args"] = "-o BatchMode=yes"
        write("inventory.json", json.dumps({"trusttunnel": {"hosts": {"managed": host}}}))
        write(
            "parameters.json",
            json.dumps(
                {
                    "trusttunnel_status_mode": "short",
                    "trusttunnel_status_log_lines": request.parameters.log_lines,
                    "trusttunnel_status_cert_warning_days": (
                        request.parameters.certificate_warning_days
                    ),
                    "trusttunnel_status_output_file": "",
                    "trusttunnel_client_config_local_dir": str(cwd / "client_configs"),
                    "ttcp_acme_http": request.preflight.acme_http if request.preflight else False,
                }
            ),
        )
        write("ansible.cfg", "[defaults]\nretry_files_enabled=False\nhost_key_checking=True\n")
        # Do not inherit application secrets, SSH agents, Ansible plugin paths or config.
        env = {
            "PATH": os.defpath,
            "HOME": str(cwd),
            "LANG": "C.UTF-8",
            "ANSIBLE_CONFIG": str(cwd / "ansible.cfg"),
            "ANSIBLE_LOCAL_TEMP": str(cwd / "tmp"),
            "ANSIBLE_STDOUT_CALLBACK": (
                "ttcp_preflight_safe" if request.operation == "server.preflight" else "ttcp_safe"
            ),
            "ANSIBLE_CALLBACK_PLUGINS": str(ROOT / "apps/execution/callback_plugins"),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_LOAD_CALLBACK_PLUGINS": "0",
            "ANSIBLE_LIBRARY": str(ROOT / "apps/execution/library"),
        }
        argv: tuple[str, ...] = (
            self.executable,
            "-i",
            str(cwd / "inventory.json"),
            "--limit",
            "managed",
            "--extra-vars",
            "@" + str(cwd / "parameters.json"),
            str(
                ROOT
                / "automation/ansible"
                / (
                    "preflight.yml"
                    if request.operation == "server.preflight"
                    else "managed-status.yml"
                )
            ),
        )
        if request.operation == "execution.validate":
            argv += ("--syntax-check",)
        return argv, env
