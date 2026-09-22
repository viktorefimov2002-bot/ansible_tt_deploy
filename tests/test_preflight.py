import errno
import json
import runpy
import socket
import sys
from types import ModuleType, SimpleNamespace

import pytest

from apps.execution.ansible import ROOT, AnsibleExecutionAdapter
from apps.execution.ports import CHECKS, ExecutionRequest, Outcome, PreflightParameters
from apps.execution.preflight import dns_checks
from tests.test_execution import collect, target


@pytest.mark.parametrize("ending", [0, 4, "timeout", "missing"])
async def test_adapter_partial_reports_are_bounded_and_secret_safe(monkeypatch, ending):
    async def dns(_):
        return {"dns_a": "pass", "dns_aaaa": "skipped"}

    monkeypatch.setattr("apps.execution.ansible.dns_checks", dns)
    events = []

    async def runner(argv, cwd, env, output, deadline):
        assert argv[-1].endswith("preflight.yml")
        assert env["ANSIBLE_STDOUT_CALLBACK"] == "ttcp_preflight_safe"
        assert "StrictHostKeyChecking=yes" in (cwd / "inventory.json").read_text()
        if ending != "missing":
            report = {
                "ssh": "pass",
                "disk": "fail",
                "memory": "private-secret",
                "unknown_key": "pass",
                "dns_a": "fail",
            }
            wire = ("ttcp_checks:" + json.dumps(report) + "\n").encode()
            await output(wire[:19], False)
            await output(wire[19:], False)
            await output(b"ttcp_checks:{malformed private-secret}\n", False)
        if ending == "timeout":
            raise TimeoutError("private-secret")
        return 0 if ending == "missing" else ending

    result = await AnsibleExecutionAdapter(runner=runner).execute(
        ExecutionRequest(
            operation="server.preflight",
            target=target(),
            preflight=PreflightParameters(
                domain="vpn.example.org", public_ip="192.0.2.5", acme_http=False
            ),
        ),
        lambda event: collect(events, event),
    )
    assert set(result.checks) == set(CHECKS)
    assert result.checks["dns_a"] == "pass" and result.checks["tcp_80"] == "skipped"
    assert result.checks["memory"] == "unknown"
    assert result.checks["disk"] == ("unknown" if ending == "missing" else "fail")
    assert "private-secret" not in repr(result) + str(events)
    if ending == 4:
        assert result.checks["ssh"] == "fail"
    if ending == "timeout":
        assert result.outcome == Outcome.TIMED_OUT and result.checks["ssh"] == "pass"


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("good", {"dns_a": "pass", "dns_aaaa": "skipped"}),
        ("wrong", {"dns_a": "fail", "dns_aaaa": "skipped"}),
        ("temporary", {"dns_a": "unknown", "dns_aaaa": "unknown"}),
        ("dual", {"dns_a": "pass", "dns_aaaa": "pass"}),
    ],
)
async def test_dns_family_matching_and_absence(monkeypatch, mode, expected):
    import asyncio

    async def addresses(host, port, *, family, type):
        if mode == "temporary":
            raise socket.gaierror(socket.EAI_AGAIN, "do-not-echo")
        if family == socket.AF_INET6:
            if mode != "dual":
                raise socket.gaierror(socket.EAI_NONAME, "do-not-echo")
            address = "2001:db8::1"
        else:
            address = "192.0.2.5" if mode != "wrong" else "192.0.2.9"
        return [(family, type, 0, "", (address, port))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", addresses)
    assert (
        await dns_checks(PreflightParameters(domain="vpn.example.org", public_ip="192.0.2.5"))
        == expected
    )


def test_preflight_callback_projects_only_reviewed_codes(monkeypatch):
    callback = ModuleType("ansible.plugins.callback")
    callback.CallbackBase = object
    monkeypatch.setitem(sys.modules, "ansible.plugins.callback", callback)
    namespace = runpy.run_path(str(ROOT / "apps/execution/callback_plugins/ttcp_preflight_safe.py"))
    instance = namespace["CallbackModule"]()
    messages = []
    instance._display = SimpleNamespace(display=messages.append)
    instance.v2_runner_on_ok(
        SimpleNamespace(
            _task=SimpleNamespace(action="ttcp_preflight"),
            _result={
                "stdout": "secret",
                "ttcp_checks": {
                    "ssh": "pass",
                    "disk": "secret",
                    "password": "pass",
                    "memory": ["secret"],
                },
            },
        )
    )
    assert json.loads(messages[-1][12:]) == {"ssh": "pass"}
    assert "secret" not in str(messages)


def test_remote_checks_and_partial_probe_failures(monkeypatch):
    basic = ModuleType("ansible.module_utils.basic")
    basic.AnsibleModule = object
    monkeypatch.setitem(sys.modules, "ansible.module_utils.basic", basic)
    namespace = runpy.run_path(str(ROOT / "apps/execution/library/ttcp_preflight.py"))
    collect_checks = namespace["collect"]
    scope = collect_checks.__globals__
    monkeypatch.setattr(scope["platform"], "machine", lambda: "x86_64")
    monkeypatch.setattr(
        scope["platform"], "freedesktop_os_release", lambda: {"ID": "debian", "VERSION_ID": "12"}
    )
    monkeypatch.setattr(scope["shutil"], "disk_usage", lambda _: SimpleNamespace(free=2 * 1024**3))
    monkeypatch.setattr(
        scope["os"],
        "sysconf",
        lambda name: 4096 if name == "SC_PAGE_SIZE" else 262144,
        raising=False,
    )
    monkeypatch.setattr(
        scope["subprocess"], "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"yes\n")
    )

    class Socket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            if address[1] == 443:
                raise OSError(errno.EADDRINUSE, "secret")

    monkeypatch.setattr(scope["socket"], "socket", Socket)
    result = collect_checks(False)
    assert result == {
        "ssh": "pass",
        "tcp_80": "skipped",
        "tcp_443": "fail",
        "udp_443": "fail",
        "os": "pass",
        "architecture": "pass",
        "disk": "pass",
        "memory": "pass",
        "time_sync": "pass",
    }
    monkeypatch.setattr(scope["platform"], "machine", lambda: "unsupported")
    monkeypatch.setattr(scope["shutil"], "disk_usage", lambda _: SimpleNamespace(free=0))
    monkeypatch.setattr(
        scope["subprocess"], "run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout=b"secret")
    )
    result = collect_checks(True)
    assert result["architecture"] == result["disk"] == "fail"
    assert result["time_sync"] == "unknown" and result["tcp_80"] == "pass"


@pytest.mark.parametrize(
    "rows,expected",
    [
        ("header\n", "pass"),
        ("header\n 0: 00000000:01BB 00000000:0000 0A\n", "fail"),
        ("header\nmalformed\n", "unknown"),
    ],
)
def test_privileged_port_occupancy_without_sudo(monkeypatch, rows, expected):
    from pathlib import Path

    basic = ModuleType("ansible.module_utils.basic")
    basic.AnsibleModule = object
    monkeypatch.setitem(sys.modules, "ansible.module_utils.basic", basic)
    namespace = runpy.run_path(str(ROOT / "apps/execution/library/ttcp_preflight.py"))
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: rows)

    class DeniedSocket:
        def __init__(self, *args):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def setsockopt(self, *args):
            pass

        def bind(self, address):
            raise OSError(errno.EACCES, "permission denied")

    monkeypatch.setattr(socket, "socket", DeniedSocket)
    assert namespace["probe_ports"](443, socket.SOCK_STREAM) == expected
