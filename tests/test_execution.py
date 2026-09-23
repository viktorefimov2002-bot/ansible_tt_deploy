import asyncio
import json
import os
import runpy
import shutil
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.execution.ansible import ROOT, AnsibleExecutionAdapter
from apps.execution.ports import ExecutionEvent, ExecutionRequest, Outcome, Target
from apps.execution.process import run_process


def target(**changes):
    values = dict(
        host="192.0.2.8", user="root", host_key="ssh-ed25519 AAAATEST", password="fixture-secret"
    )
    values.update(changes)
    return Target(**values)


async def collect(events, event):
    events.append(event)


@pytest.mark.parametrize(
    "code,outcome",
    [(0, Outcome.SUCCEEDED), (2, Outcome.FAILED), (4, Outcome.UNREACHABLE), (-9, Outcome.FAILED)],
)
@pytest.mark.parametrize("auth", ["password", "private_key"])
async def test_adapter_contract_and_secret_safe_stream(code, outcome, auth, monkeypatch):
    events, directories = [], []
    monkeypatch.setenv("ANSIBLE_STDOUT_CALLBACK", "malicious")
    monkeypatch.setenv("TTCP_POSTGRES_PASSWORD", "application-secret")
    selected = target() if auth == "password" else target(password=None, private_key="fixture-key")

    async def fake(argv, cwd, env, output, deadline):
        directories.append(cwd)
        assert cwd.is_dir()
        assert "application-secret" not in str(env)
        assert env["ANSIBLE_STDOUT_CALLBACK"] == "ttcp_safe"
        assert "fixture-secret" not in str(argv) + str(env)
        assert "fixture-key" not in str(argv) + str(env)
        assert argv[-1] == str(ROOT / "automation/ansible/managed-status.yml")
        assert argv[argv.index("--limit") + 1] == "managed"
        inventory = json.loads((cwd / "inventory.json").read_text())
        hosts = inventory["trusttunnel"]["hosts"]
        assert list(hosts) == ["managed"]
        assert hosts["managed"]["ansible_host"] == "192.0.2.8"
        assert "StrictHostKeyChecking=yes" in hosts["managed"]["ansible_ssh_args"]
        if auth == "password":
            assert hosts["managed"]["ansible_password"] == "fixture-secret"
        else:
            assert (cwd / "identity").read_text() == "fixture-key"
        if os.name == "posix":
            assert cwd.stat().st_mode & 0o777 == 0o700
            assert (cwd / "inventory.json").stat().st_mode & 0o777 == 0o600
        await output(b"execution_ta", False)
        await output(b"sk_ok\npassword=fixture-secret\n", False)
        await output(b"fixture-key", True)
        await output(b"x" * 100000, False)
        return code

    result = await AnsibleExecutionAdapter(runner=fake).execute(
        ExecutionRequest(operation="server.status", target=selected),
        lambda event: collect(events, event),
    )
    assert (result.outcome, result.exit_code) == (outcome, code)
    assert ExecutionEvent.TASK_OK in events
    assert events.count(ExecutionEvent.OUTPUT_SUPPRESSED) == 1
    assert not any(path.exists() for path in directories)
    assert "fixture-secret" not in str(events) + repr(selected) + selected.model_dump_json()


@pytest.mark.parametrize(
    "changes",
    [
        {"host": "-oProxyCommand=evil"},
        {"host": "host;touch /tmp/pwn"},
        {"host": "$(id)"},
        {"host": "{{lookup('pipe','id')}}"},
        {"user": "root --help"},
        {"port": "22"},
        {"port": 0},
        {"host_key": "ssh-ed25519 AAAA\nevil"},
        {"password": "{{lookup('pipe','id')}}"},
        {"password": ""},
        {"private_key": "key"},
        {"inventory": "/etc/passwd"},
    ],
)
def test_target_rejects_injection(changes):
    with pytest.raises(ValidationError):
        target(**changes)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "../../site.yml"},
        {"operation": "server.deploy"},
        {"operation": "server.status"},
        {"operation": "execution.validate", "playbook": "site.yml"},
        {"operation": "execution.validate", "extra_vars": {"ansible_connection": "local"}},
        {"operation": "execution.validate", "parameters": {"log_lines": "1;id"}},
        {"operation": "execution.validate", "parameters": {"log_lines": 201}},
        {"operation": "execution.validate", "timeout_seconds": 46},
    ],
)
def test_closed_request(payload):
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate(payload)


async def test_forged_models_are_revalidated_before_process_start():
    async def forbidden(*args):
        pytest.fail("Invalid request reached executor")

    adapter = AnsibleExecutionAdapter(runner=forbidden)
    request = ExecutionRequest.model_construct(operation="../../evil")
    assert (await adapter.execute(request, forbidden)).outcome == Outcome.INVALID
    request = ExecutionRequest(operation="server.status", target=target()).model_copy(
        update={"target": target().model_copy(update={"host": "{{evil}}"})}
    )
    assert (await adapter.execute(request, forbidden)).outcome == Outcome.INVALID


@pytest.mark.parametrize(
    "failure,outcome", [(TimeoutError, Outcome.TIMED_OUT), (FileNotFoundError, Outcome.UNAVAILABLE)]
)
async def test_runtime_failure_and_cleanup(failure, outcome):
    directories = []

    async def fail(argv, cwd, *args):
        directories.append(cwd)
        raise failure("secret must not escape")

    events = []
    result = await AnsibleExecutionAdapter(runner=fail).execute(
        ExecutionRequest(operation="execution.validate"), lambda e: collect(events, e)
    )
    assert result.outcome == outcome
    assert not directories[0].exists()
    assert "secret" not in repr(result) + str(events)


async def test_adapter_cancellation_waits_for_cleanup():
    started, cleaned = asyncio.Event(), asyncio.Event()
    directories = []

    async def slow(argv, cwd, *args):
        directories.append(cwd)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            assert cwd.exists()
            cleaned.set()

    events = []
    task = asyncio.create_task(
        AnsibleExecutionAdapter(runner=slow).execute(
            ExecutionRequest(operation="server.status", target=target()),
            lambda e: collect(events, e),
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and not directories[0].exists()


async def test_output_event_volume_is_bounded():
    async def flood(argv, cwd, env, output, deadline):
        for _ in range(10):
            await output(b"execution_task_ok\n" * 1000, False)
        return 0

    events = []
    result = await AnsibleExecutionAdapter(runner=flood).execute(
        ExecutionRequest(operation="execution.validate"), lambda e: collect(events, e)
    )
    assert result.outcome == Outcome.SUCCEEDED
    assert len(events) == 151  # Started plus bounded callback output.


def test_callback_never_reads_secret_result_data(monkeypatch):
    callback = ModuleType("ansible.plugins.callback")
    callback.CallbackBase = object
    monkeypatch.setitem(sys.modules, "ansible.plugins.callback", callback)
    module = runpy.run_path(str(ROOT / "apps/execution/callback_plugins/ttcp_safe.py"))
    instance = module["CallbackModule"]()
    messages = []
    instance._display = SimpleNamespace(display=messages.append)

    class SecretResult:
        def __getattribute__(self, name):
            raise AssertionError("Callback must not inspect raw results")

    for event in ("ok", "failed", "unreachable", "skipped"):
        getattr(instance, f"v2_runner_on_{event}")(SecretResult())
    assert messages == [
        "execution_task_ok",
        "execution_task_failed",
        "execution_unreachable",
        "execution_task_skipped",
    ]


async def test_password_shell_metacharacters_remain_literal_inventory_data():
    secret = "test-only $(id); `whoami` & 'quote' \\ unicode-π\nnewline"

    async def runner(argv, cwd, env, output, deadline):
        data = json.loads((cwd / "inventory.json").read_text())
        assert data["trusttunnel"]["hosts"]["managed"]["ansible_password"] == secret
        assert secret not in str(argv) + str(env)
        return 0

    events = []
    result = await AnsibleExecutionAdapter(runner=runner).execute(
        ExecutionRequest(operation="server.status", target=target(password=secret)),
        lambda e: collect(events, e),
    )
    assert result.outcome == Outcome.SUCCEEDED


@pytest.mark.parametrize("failure", ["timeout", "cancel", "spawn_cancel", "output_failure"])
async def test_process_cleanup_contract_with_fake_os(tmp_path, monkeypatch, failure):
    import apps.execution.process as module

    spawned, release_spawn, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()
    signals = []
    process = SimpleNamespace(
        pid=123, returncode=None, stdout=asyncio.StreamReader(), stderr=asyncio.StreamReader()
    )

    async def wait():
        await exited.wait()
        return process.returncode

    process.wait = wait

    def killpg(pid, sig):
        assert pid == 123
        signals.append(sig)
        if sig == 9:
            process.returncode = -9
            process.stdout.feed_eof()
            process.stderr.feed_eof()
            exited.set()

    async def spawn(*argv, **kwargs):
        assert argv == ("approved-executable", "argument with spaces")
        assert kwargs["start_new_session"] is True
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        spawned.set()
        if failure == "spawn_cancel":
            await release_spawn.wait()
        return process

    async def output(chunk, stderr):
        raise ValueError("sink failed")

    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", killpg=killpg))
    monkeypatch.setattr(module, "signal", SimpleNamespace(SIGTERM=15, SIGKILL=9))
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    task = asyncio.create_task(
        module.run_process(
            ("approved-executable", "argument with spaces"), tmp_path, {}, output, 0.05
        )
    )
    await spawned.wait()
    if failure in {"cancel", "spawn_cancel"}:
        task.cancel()
        release_spawn.set()
        await asyncio.sleep(0.01)
        task.cancel()  # Cleanup must survive repeated cancellation.
        expected = asyncio.CancelledError
    elif failure == "output_failure":
        process.stdout.feed_data(b"output")
        expected = ExceptionGroup
    else:
        expected = TimeoutError
    with pytest.raises(expected):
        await task
    assert signals == [15, 9] and exited.is_set()


@pytest.mark.skipif(os.name != "posix", reason="Ansible controller/process groups require POSIX")
@pytest.mark.parametrize("cancel", [False, True])
async def test_real_process_terminates_descendants_and_drains_both_pipes(tmp_path, cancel):
    started = asyncio.Event()
    captured = bytearray()

    async def output(chunk, stderr):
        if not stderr:
            captured.extend(chunk)
            if b"\n" in captured:
                started.set()

    code = (
        "import subprocess,sys,time; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        "print(p.pid,flush=True); "
        "sys.stderr.write('x'*100000); sys.stderr.flush(); time.sleep(60)"
    )
    task = asyncio.create_task(
        run_process((sys.executable, "-c", code), tmp_path, {"PATH": os.defpath}, output, 2)
    )
    await asyncio.wait_for(started.wait(), 1)
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    pid = int(captured.split(b"\n")[0])
    # Orphan zombies can await container init; a zombie cannot continue work.
    stat = Path(f"/proc/{pid}/stat")
    if await asyncio.to_thread(stat.exists):
        assert (await asyncio.to_thread(stat.read_text)).split()[2] == "Z"
    else:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.mark.skipif(os.name != "posix", reason="Real Ansible controller requires POSIX")
async def test_real_ansible_adapter_smoke():
    executable = shutil.which("ansible-playbook")
    if executable is None:
        pytest.skip("Install requirements-execution.lock to run the real adapter smoke")
    events = []
    result = await AnsibleExecutionAdapter(executable=executable).execute(
        ExecutionRequest(operation="execution.validate"), lambda e: collect(events, e)
    )
    assert result.outcome == Outcome.SUCCEEDED
    assert result.exit_code == 0
